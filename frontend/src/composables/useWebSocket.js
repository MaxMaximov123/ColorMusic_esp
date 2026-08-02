import { ref, onUnmounted } from 'vue'

export function useWebSocket() {
  const devices = ref([])
  const connected = ref(false)
  const calibrationResult = ref(null)

  let ws = null
  let reconnectTimer = null

  function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/client`

    ws = new WebSocket(url)

    ws.onopen = () => {
      connected.value = true
    }

    ws.onclose = () => {
      connected.value = false
      ws = null
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
