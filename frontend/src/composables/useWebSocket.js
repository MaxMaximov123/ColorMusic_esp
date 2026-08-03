import { ref, onUnmounted } from 'vue'
import { useAuth } from './useAuth.js'

export function useWebSocket() {
  const devices = ref([])
  const connected = ref(false)
  const calibrationResult = ref(null)

  const { getToken } = useAuth()

  let ws = null
  let reconnectTimer = null

  function connect() {
    const token = getToken()
    if (!token) return

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/client?token=${encodeURIComponent(token)}`

    ws = new WebSocket(url)

    ws.onopen = () => {
      connected.value = true
    }

    ws.onclose = (event) => {
      connected.value = false
      ws = null

      // Code 1008 or immediate close may mean auth rejected
      if (event.code === 1008 || event.code === 4001) {
        // Auth rejected, redirect to login
        localStorage.removeItem('token')
        window.location.href = '/login'
        return
      }

      scheduleReconnect()
    }

    ws.onerror = () => {
      if (ws) {
        ws.close()
      }
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        handleMessage(msg)
      } catch (e) {
        console.error('WS parse error:', e)
      }
    }
  }

  function handleMessage(msg) {
    if (msg.type === 'devices') {
      devices.value = msg.devices || []
    } else if (msg.type === 'state') {
      const idx = devices.value.findIndex(d => d.id === msg.deviceId)
      if (idx !== -1) {
        devices.value[idx] = {
          ...devices.value[idx],
          state: { ...devices.value[idx].state, ...msg.state }
        }
      }
    } else if (msg.type === 'deviceOnline') {
      const idx = devices.value.findIndex(d => d.id === msg.deviceId)
      if (idx !== -1) {
        devices.value[idx] = { ...devices.value[idx], online: msg.online }
      }
    } else if (msg.type === 'calibrate') {
      calibrationResult.value = {
        deviceId: msg.deviceId,
        values: msg.values
      }
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, 3000)
  }

  function sendCommand(deviceId, params) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'set',
        deviceId,
        params
      }))
    }
  }

  function requestCalibrate(deviceId) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      calibrationResult.value = null
      ws.send(JSON.stringify({
        type: 'calibrate',
        deviceId
      }))
    }
  }

  connect()

  onUnmounted(() => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
    }
    if (ws) {
      ws.close()
    }
  })

  return {
    devices,
    connected,
    calibrationResult,
    sendCommand,
    requestCalibrate
  }
}
