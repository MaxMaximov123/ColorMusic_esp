<script setup>
import { ref, onMounted, onUnmounted, computed, nextTick } from 'vue'
import { useAuth } from '../composables/useAuth.js'

const { getToken } = useAuth()

const ipeyeLogin = ref('')
const ipeyePassword = ref('')
const ipeyeSession = ref(null)
const cameras = ref([])
const selectedCamera = ref(null)
const streaming = ref(false)
const loginLoading = ref(false)
const loginError = ref('')
const streamStatus = ref('')
const streamError = ref('')
const isFullscreen = ref(false)
const autoConnecting = ref(true)
const cameraMode = ref(null) // 'rtsp' | 'ipeye'

const videoRef = ref(null)
const viewportRef = ref(null)

let ws = null
let mediaSource = null
let sourceBuffer = null
let appendQueue = []
let streamingStarted = false
let liveEdgeTimer = null
let gopDecode = null
let reconnectTimer = null
let reconnectCount = 0

// Zoom (transform-origin: 0 0; transform: translate(px,py) scale(S))
const zoomScale = ref(1)
const panX = ref(0)
const panY = ref(0)
let pinchStartDist = 0
let pinchStartScale = 1
let pinchStartPanX = 0
let pinchStartPanY = 0
let pinchMidX = 0
let pinchMidY = 0
let panTouchStartX = 0
let panTouchStartY = 0
let panStartPX = 0
let panStartPY = 0
let isPinching = false

// Swipe
const swipeOffset = ref(0)
let touchStartX = 0
let touchStartY = 0
let touchStartTime = 0
let lastTapTime = 0
let swipeDecided = false
let isHorizontalSwipe = false

// Mouse drag
let mouseDragging = false
let mouseStartX = 0
let mouseStartY = 0
let mouseStartPX = 0
let mouseStartPY = 0

const currentCameraName = computed(() => {
  const cam = cameras.value.find(c => c.id === selectedCamera.value)
  return cam ? (cam.name || cam.id) : ''
})

const currentCameraIndex = computed(() =>
  cameras.value.findIndex(c => c.id === selectedCamera.value)
)

const zoomStyle = computed(() => {
  if (zoomScale.value <= 1 && panX.value === 0 && panY.value === 0) return {}
  return {
    transform: `translate(${panX.value}px, ${panY.value}px) scale(${zoomScale.value})`,
    transformOrigin: '0 0',
    willChange: 'transform'
  }
})

const viewportCursor = computed(() => {
  if (zoomScale.value > 1) return mouseDragging ? 'grabbing' : 'grab'
  return 'default'
})

function onFullscreenChange() {
  if (!document.fullscreenElement && !document.webkitFullscreenElement) {
    isFullscreen.value = false
  }
}

onMounted(async () => {
  document.addEventListener('fullscreenchange', onFullscreenChange)
  document.addEventListener('webkitfullscreenchange', onFullscreenChange)

  const headers = { 'Authorization': `Bearer ${getToken()}` }

  // Try RTSP cameras first
  try {
    const resp = await fetch('/api/cameras', { headers })
    const cams = await resp.json()
    if (cams.length > 0) {
      cameraMode.value = 'rtsp'
      cameras.value = cams
      selectedCamera.value = cams[0].id
      autoConnecting.value = false
      await nextTick()
      startStream()
      return
    }
  } catch {}

  // Fallback to IPeye
  try {
    const resp = await fetch('/api/ipeye/connect', { headers })
    const data = await resp.json()
    if (data.session) {
      cameraMode.value = 'ipeye'
      ipeyeSession.value = data.session
      cameras.value = data.cameras || []
      if (cameras.value.length > 0) {
        selectedCamera.value = cameras.value[0].id
        await nextTick()
        startStream()
      }
    }
  } catch {}
  autoConnecting.value = false
})

onUnmounted(() => {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  stopStream()
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.removeEventListener('webkitfullscreenchange', onFullscreenChange)
})

async function doIpeyeLogin() {
  loginLoading.value = true
  loginError.value = ''
  try {
    const resp = await fetch('/api/ipeye/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getToken()}` },
      body: JSON.stringify({ login: ipeyeLogin.value, password: ipeyePassword.value })
    })
    const data = await resp.json()
    if (!resp.ok) { loginError.value = data.error || 'Ошибка авторизации'; return }
    cameraMode.value = 'ipeye'
    ipeyeSession.value = data.session
    cameras.value = data.cameras || []
    if (cameras.value.length > 0) {
      selectedCamera.value = cameras.value[0].id
      await nextTick()
      startStream()
    }
    fetch('/api/ipeye/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getToken()}` },
      body: JSON.stringify({ login: ipeyeLogin.value, password: ipeyePassword.value })
    }).catch(() => {})
  } catch { loginError.value = 'Сервис видеонаблюдения недоступен' }
  finally { loginLoading.value = false }
}

// --- Stream ---
function startStream() {
  if (!selectedCamera.value) return
  if (cameraMode.value === 'ipeye' && !ipeyeSession.value) return
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  if (ws) { ws.onclose = null; ws.onerror = null; ws.close(); ws = null }
  cleanupMse()

  streamError.value = ''
  streamStatus.value = 'Подключение...'
  streaming.value = true

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = encodeURIComponent(getToken())
  let wsUrl
  if (cameraMode.value === 'rtsp') {
    wsUrl = `${protocol}//${location.host}/ws/rtsp?camera=${encodeURIComponent(selectedCamera.value)}&token=${token}`
  } else {
    wsUrl = `${protocol}//${location.host}/ws/camera?session=${encodeURIComponent(ipeyeSession.value)}&camera=${encodeURIComponent(selectedCamera.value)}&token=${token}`
  }
  connectToStream(wsUrl)
}

function connectToStream(wsUrl) {
  sourceBuffer = null
  appendQueue = []
  streamingStarted = false
  gopDecode = null

  const video = videoRef.value
  if (!video) return

  const MSrc = window.ManagedMediaSource || window.MediaSource
  let pendingPackets = []

  function initMse(codecStr) {
    cleanupMse()
    pendingPackets = []

    mediaSource = new MSrc()
    video.src = URL.createObjectURL(mediaSource)

    mediaSource.addEventListener('sourceopen', () => {
      if (!mediaSource || mediaSource.readyState !== 'open') return
      const mimeType = `video/mp4; codecs="${codecStr}"`
      if (!MSrc.isTypeSupported(mimeType)) {
        streamError.value = 'Браузер не поддерживает кодек: ' + codecStr
        stopStream()
        return
      }
      sourceBuffer = mediaSource.addSourceBuffer(mimeType)
      sourceBuffer.mode = 'segments'
      mediaSource.duration = Infinity
      sourceBuffer.addEventListener('updateend', onUpdateEnd)
      sourceBuffer.addEventListener('error', () => {
        streamError.value = 'Ошибка декодирования видео'
      })
      if (pendingPackets.length > 0) {
        for (const pkt of pendingPackets) pushPacket(pkt)
        pendingPackets = []
        if (video.paused) video.play().catch(() => {})
      }
    })
  }

  ws = new WebSocket(wsUrl)
  ws.binaryType = 'arraybuffer'

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      try {
        const msg = JSON.parse(event.data)
        if (msg.error) { streamError.value = msg.error; stopStream(); return }
        if (msg.status === 'connected') {
          streamStatus.value = 'Ожидание видеоданных...'
          reconnectCount = 0
        }
        if (msg.status === 'connecting') {
          streamStatus.value = 'Подключение к камере...'
        }
        if (msg.status === 'reconnecting') {
          streamStatus.value = 'Камера переподключается...'
        }
      } catch {}
      return
    }

    const data = new Uint8Array(event.data)
    if (data[0] === 6) {
      const codecStr = new TextDecoder().decode(data.slice(1))
      streamStatus.value = ''
      initMse(codecStr)
      return
    }

    if (!sourceBuffer) {
      pendingPackets.push(event.data)
      return
    }
    pushPacket(event.data)
    if (video.paused) video.play().catch(() => {})
  }

  ws.onclose = () => {
    ws = null
    streaming.value = false
    streamStatus.value = ''
    cleanupMse()
    if (!streamError.value) scheduleReconnect()
  }

  ws.onerror = () => {
    streamError.value = 'Ошибка соединения с камерой'
  }
}

function pushPacket(packet) {
  const view = new Uint8Array(packet)
  if (!streamingStarted && !sourceBuffer.updating) {
    try {
      sourceBuffer.appendBuffer(view)
      streamingStarted = true
    } catch {
      streamError.value = 'Ошибка буфера видео'
      stopStream()
    }
    return
  }
  appendQueue.push(view)
  if (!sourceBuffer.updating) loadPacket()
}

function loadPacket() {
  if (!sourceBuffer || sourceBuffer.updating) return
  if (appendQueue.length > 0) {
    try { sourceBuffer.appendBuffer(appendQueue.shift()) }
    catch { stopStream() }
  } else {
    streamingStarted = false
  }
}

function onUpdateEnd() {
  if (!sourceBuffer) return
  if (sourceBuffer.buffered.length > 0) {
    const range = sourceBuffer.buffered.length - 1
    const bufferedEnd = sourceBuffer.buffered.end(range)

    if (streamStatus.value) {
      streamStatus.value = ''
      startLiveEdgeSeeker()
    }

    if (gopDecode === null && videoRef.value) {
      gopDecode = Math.abs(bufferedEnd - videoRef.value.currentTime)
    }

    const video = videoRef.value
    if (video) {
      const avgBuf = gopDecode !== null ? Math.max(gopDecode, 0.9) : 2
      if (bufferedEnd - video.currentTime >= avgBuf * 3) {
        video.currentTime = bufferedEnd - avgBuf * 1.5
      }
    }

    const start = sourceBuffer.buffered.start(0)
    if (bufferedEnd - start > 30) {
      try { sourceBuffer.remove(start, bufferedEnd - 10); return } catch {}
    }
  }
  loadPacket()
}

function startLiveEdgeSeeker() {
  if (liveEdgeTimer) return
  liveEdgeTimer = setInterval(() => {
    const video = videoRef.value
    if (!video || !sourceBuffer || !sourceBuffer.buffered.length) return
    const end = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1)
    const avgBuf = gopDecode !== null ? Math.max(gopDecode, 0.9) : 2
    if (end - video.currentTime > avgBuf * 3) video.currentTime = end - avgBuf * 1.5
  }, 3000)
}

function cleanupMse() {
  appendQueue = []
  streamingStarted = false
  gopDecode = null
  if (liveEdgeTimer) { clearInterval(liveEdgeTimer); liveEdgeTimer = null }
  if (sourceBuffer) {
    try {
      sourceBuffer.removeEventListener('updateend', onUpdateEnd)
      if (mediaSource && mediaSource.readyState === 'open') mediaSource.removeSourceBuffer(sourceBuffer)
    } catch {}
    sourceBuffer = null
  }
  if (mediaSource && mediaSource.readyState === 'open') {
    try { mediaSource.endOfStream() } catch {}
  }
  mediaSource = null
  const video = videoRef.value
  if (video) {
    URL.revokeObjectURL(video.src)
    video.src = ''
    video.load()
  }
}

function stopStream() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  reconnectCount = 0
  if (ws) { ws.onclose = null; ws.onerror = null; ws.close(); ws = null }
  streaming.value = false
  streamStatus.value = ''
  cleanupMse()
}

function scheduleReconnect() {
  if (cameraMode.value !== 'rtsp' || !selectedCamera.value) return
  if (reconnectCount >= 10) return
  reconnectCount++
  const delay = Math.min(1000 * Math.pow(1.5, reconnectCount - 1), 10000)
  streamStatus.value = 'Переподключение...'
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    if (selectedCamera.value) startStream()
  }, delay)
}

// --- Camera switching ---
async function switchCamera(index) {
  if (index < 0 || index >= cameras.value.length) return
  if (cameras.value[index].id === selectedCamera.value) return
  stopStream()
  resetZoom()
  selectedCamera.value = cameras.value[index].id
  await nextTick()
  startStream()
}

// --- Zoom ---
function resetZoom() {
  zoomScale.value = 1
  panX.value = 0
  panY.value = 0
}

function constrainPan() {
  const el = viewportRef.value
  if (!el || zoomScale.value <= 1) return
  const S = zoomScale.value
  const w = el.clientWidth
  const h = el.clientHeight
  panX.value = Math.max(w * (1 - S), Math.min(0, panX.value))
  panY.value = Math.max(h * (1 - S), Math.min(0, panY.value))
}

function zoomAtPoint(newScale, vpX, vpY) {
  const oldScale = zoomScale.value
  if (newScale <= 1) { resetZoom(); return }
  newScale = Math.min(5, newScale)
  const ratio = newScale / oldScale
  panX.value = vpX - (vpX - panX.value) * ratio
  panY.value = vpY - (vpY - panY.value) * ratio
  zoomScale.value = newScale
  constrainPan()
}

// --- Touch ---
function onTouchStart(e) {
  swipeDecided = false
  isHorizontalSwipe = false

  if (e.touches.length === 2) {
    isPinching = true
    const [t1, t2] = e.touches
    pinchStartDist = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY)
    pinchStartScale = zoomScale.value
    pinchStartPanX = panX.value
    pinchStartPanY = panY.value
    const rect = viewportRef.value.getBoundingClientRect()
    pinchMidX = (t1.clientX + t2.clientX) / 2 - rect.left
    pinchMidY = (t1.clientY + t2.clientY) / 2 - rect.top
    e.preventDefault()
    return
  }

  if (e.touches.length === 1) {
    isPinching = false
    const t = e.touches[0]
    touchStartX = t.clientX
    touchStartY = t.clientY
    touchStartTime = Date.now()
    panTouchStartX = t.clientX
    panTouchStartY = t.clientY
    panStartPX = panX.value
    panStartPY = panY.value
  }
}

function onTouchMove(e) {
  if (isPinching && e.touches.length >= 2) {
    e.preventDefault()
    const [t1, t2] = e.touches
    const dist = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY)
    const newScale = Math.min(5, Math.max(1, pinchStartScale * dist / pinchStartDist))

    const rect = viewportRef.value.getBoundingClientRect()
    const curMidX = (t1.clientX + t2.clientX) / 2 - rect.left
    const curMidY = (t1.clientY + t2.clientY) / 2 - rect.top

    const ratio = newScale / pinchStartScale
    panX.value = curMidX - (pinchMidX - pinchStartPanX) * ratio
    panY.value = curMidY - (pinchMidY - pinchStartPanY) * ratio
    zoomScale.value = newScale

    if (newScale <= 1) { panX.value = 0; panY.value = 0 }
    else constrainPan()
    return
  }

  if (e.touches.length !== 1 || isPinching) return
  const t = e.touches[0]
  const dx = t.clientX - touchStartX
  const dy = t.clientY - touchStartY

  if (!swipeDecided && (Math.abs(dx) > 8 || Math.abs(dy) > 8)) {
    swipeDecided = true
    isHorizontalSwipe = Math.abs(dx) > Math.abs(dy)
  }

  if (zoomScale.value > 1) {
    e.preventDefault()
    panX.value = panStartPX + (t.clientX - panTouchStartX)
    panY.value = panStartPY + (t.clientY - panTouchStartY)
    constrainPan()
    return
  }

  if (isHorizontalSwipe && cameras.value.length > 1) {
    e.preventDefault()
    const max = viewportRef.value ? viewportRef.value.clientWidth * 0.25 : 80
    swipeOffset.value = Math.max(-max, Math.min(max, dx * 0.3))
  }
}

function onTouchEnd(e) {
  if (isPinching) {
    isPinching = false
    if (zoomScale.value <= 1.05) resetZoom()
    else constrainPan()
    return
  }

  swipeOffset.value = 0
  if (e.changedTouches.length !== 1) return
  const t = e.changedTouches[0]
  const dx = t.clientX - touchStartX
  const dy = t.clientY - touchStartY
  const dt = Date.now() - touchStartTime

  if (zoomScale.value <= 1 && Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 2 && dt < 500) {
    const idx = currentCameraIndex.value
    if (dx < 0 && idx < cameras.value.length - 1) switchCamera(idx + 1)
    else if (dx > 0 && idx > 0) switchCamera(idx - 1)
    return
  }

  const now = Date.now()
  if (now - lastTapTime < 300 && Math.abs(dx) < 10 && Math.abs(dy) < 10) {
    if (zoomScale.value > 1) {
      resetZoom()
    } else {
      const rect = viewportRef.value.getBoundingClientRect()
      zoomAtPoint(2.5, t.clientX - rect.left, t.clientY - rect.top)
    }
    lastTapTime = 0
    return
  }
  lastTapTime = now
}

// --- Mouse ---
function onWheel(e) {
  const delta = e.deltaY > 0 ? -0.3 : 0.3
  const rect = viewportRef.value.getBoundingClientRect()
  zoomAtPoint(zoomScale.value + delta, e.clientX - rect.left, e.clientY - rect.top)
}

function onMouseDown(e) {
  if (zoomScale.value <= 1) return
  mouseDragging = true
  mouseStartX = e.clientX
  mouseStartY = e.clientY
  mouseStartPX = panX.value
  mouseStartPY = panY.value
  e.preventDefault()
}

function onMouseMove(e) {
  if (!mouseDragging) return
  panX.value = mouseStartPX + (e.clientX - mouseStartX)
  panY.value = mouseStartPY + (e.clientY - mouseStartY)
  constrainPan()
}

function onMouseUp() { mouseDragging = false }

function onDblClick(e) {
  if (zoomScale.value > 1) {
    resetZoom()
  } else {
    const rect = viewportRef.value.getBoundingClientRect()
    zoomAtPoint(2.5, e.clientX - rect.left, e.clientY - rect.top)
  }
}

// --- Fullscreen ---
function toggleFullscreen() {
  if (isFullscreen.value) {
    isFullscreen.value = false
    try {
      if (document.fullscreenElement) document.exitFullscreen()
      else if (document.webkitFullscreenElement) document.webkitExitFullscreen()
    } catch {}
    try { screen.orientation.unlock() } catch {}
  } else {
    const el = viewportRef.value
    if (!el) return
    isFullscreen.value = true
    const fn = el.requestFullscreen || el.webkitRequestFullscreen
    if (fn) fn.call(el)
    try { screen.orientation.lock('landscape').catch(() => {}) } catch {}
  }
}

function doIpeyeLogout() {
  stopStream()
  ipeyeSession.value = null
  cameras.value = []
  ipeyeLogin.value = ''
  ipeyePassword.value = ''
  fetch('/api/ipeye/forget', { method: 'DELETE', headers: { 'Authorization': `Bearer ${getToken()}` } }).catch(() => {})
}
</script>

<template>
  <div class="camera-panel">
    <!-- Loading -->
    <div v-if="autoConnecting" class="cam-loading">
      <q-spinner color="primary" size="2em" />
      <div class="q-mt-sm text-grey-6 text-caption">Подключение к камерам...</div>
    </div>

    <!-- Login (IPeye mode only) -->
    <q-card v-else-if="!ipeyeSession && cameraMode !== 'rtsp'" dark class="section-card">
      <q-card-section>
        <div class="section-title">Видеонаблюдение</div>
        <q-input v-model="ipeyeLogin" filled dense dark label="Логин IPeye" class="q-mb-sm" />
        <q-input v-model="ipeyePassword" filled dense dark label="Пароль" type="password"
                 class="q-mb-sm" @keyup.enter="doIpeyeLogin" />
        <q-btn label="Войти" color="primary" no-caps class="full-width"
               :loading="loginLoading" @click="doIpeyeLogin" />
        <div v-if="loginError" class="text-negative q-mt-sm text-caption">{{ loginError }}</div>
      </q-card-section>
    </q-card>

    <!-- Camera viewer -->
    <template v-if="ipeyeSession || cameraMode === 'rtsp'">
      <div v-if="cameras.length === 0 && !autoConnecting" class="cam-empty">
        <span class="material-icons" style="font-size:48px;color:#555;">videocam_off</span>
        <div class="q-mt-sm text-grey-6">Нет доступных камер</div>
        <q-btn flat dense color="grey-5" no-caps label="Выйти" class="q-mt-md" @click="doIpeyeLogout" />
      </div>

      <q-card v-else dark class="section-card camera-card">
        <q-card-section class="q-pa-none">
          <div ref="viewportRef" class="video-viewport"
               :style="{ cursor: viewportCursor }"
               @touchstart="onTouchStart"
               @touchmove="onTouchMove"
               @touchend="onTouchEnd"
               @wheel.prevent="onWheel"
               @dblclick="onDblClick"
               @mousedown="onMouseDown"
               @mousemove="onMouseMove"
               @mouseup="onMouseUp"
               @mouseleave="onMouseUp">

            <div class="video-track" :style="{ transform: `translateX(${swipeOffset}px)` }">
              <video ref="videoRef" autoplay muted playsinline
                     class="video-el" :style="zoomStyle" />
            </div>

            <!-- Top controls: name + buttons in one bar -->
            <div class="ctrl-top" :class="{ 'ctrl-top--fs': isFullscreen }">
              <div class="ctrl-name-area">
                <span v-if="zoomScale > 1" class="zoom-pill">{{ zoomScale.toFixed(1) }}x</span>
                <span class="ctrl-name">{{ currentCameraName }}</span>
              </div>
              <div class="ctrl-actions">
                <button v-if="zoomScale > 1" class="ctrl-btn" @click.stop="resetZoom" title="Сбросить зум">
                  <span class="material-icons">zoom_out_map</span>
                </button>
                <button class="ctrl-btn" @click.stop="toggleFullscreen"
                        :title="isFullscreen ? 'Выйти из полного экрана' : 'Полный экран'">
                  <span class="material-icons">{{ isFullscreen ? 'fullscreen_exit' : 'fullscreen' }}</span>
                </button>
                <button v-if="cameraMode === 'ipeye'" class="ctrl-btn ctrl-btn--danger" @click.stop="doIpeyeLogout" title="Выйти из аккаунта">
                  <span class="material-icons">logout</span>
                </button>
              </div>
            </div>

            <!-- Bottom controls: dot indicators -->
            <div v-if="cameras.length > 1" class="ctrl-bottom">
              <div class="cam-dots">
                <span v-for="(cam, i) in cameras" :key="cam.id"
                      class="cam-dot" :class="{ active: cam.id === selectedCamera }"
                      @click.stop="switchCamera(i)" />
              </div>
            </div>

            <!-- Status overlay -->
            <div v-if="streamStatus" class="stream-overlay">
              <q-spinner color="primary" size="2em" />
              <div class="q-mt-sm">{{ streamStatus }}</div>
            </div>

            <!-- Error overlay -->
            <div v-if="streamError && !streamStatus" class="stream-overlay">
              <span class="material-icons q-mb-sm" style="font-size:36px;color:#f44336;">error_outline</span>
              <div class="text-negative q-mb-md" style="max-width:240px;text-align:center;">{{ streamError }}</div>
              <q-btn outline dense color="primary" no-caps label="Повторить" icon="refresh" @click="startStream" />
            </div>
          </div>
        </q-card-section>
      </q-card>
    </template>
  </div>
</template>

<style scoped>
.section-card { background: #1a1a2e !important; border-radius: 8px; }
.section-title { font-size: 0.85rem; font-weight: 600; color: #aaa; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }

.cam-loading, .cam-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 48px 16px; text-align: center;
}

.camera-card { overflow: hidden; }

/* --- Viewport --- */
.video-viewport {
  position: relative;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  min-height: 200px;
  line-height: 0;
}

.video-track {
  width: 100%;
  transition: transform 0.15s ease-out;
}

.video-el {
  display: block;
  width: 100%;
  height: auto;
}

/* --- Top controls --- */
.ctrl-top {
  position: absolute;
  top: 0; left: 0; right: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 6px 22px;
  background: linear-gradient(180deg, rgba(0,0,0,0.6) 0%, rgba(0,0,0,0.15) 70%, transparent 100%);
  pointer-events: none;
  z-index: 4;
}
.ctrl-top--fs { padding: 14px 14px 28px; }

.ctrl-name-area {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
}

.ctrl-name {
  color: #fff;
  font-size: 0.85rem;
  font-weight: 500;
  text-shadow: 0 1px 4px rgba(0,0,0,0.9);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.zoom-pill {
  background: rgba(255,255,255,0.18);
  color: #fff;
  font-size: 0.7rem;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 8px;
  white-space: nowrap;
  flex-shrink: 0;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}

.ctrl-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  pointer-events: auto;
}

.ctrl-btn {
  width: 34px; height: 34px;
  border-radius: 50%;
  border: none;
  cursor: pointer;
  background: rgba(0,0,0,0.35);
  color: rgba(255,255,255,0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}
.ctrl-btn:active { background: rgba(255,255,255,0.15); }
.ctrl-btn .material-icons { font-size: 20px; }
.ctrl-btn--danger:active { background: rgba(244,67,54,0.3); color: #f44336; }

.ctrl-top--fs .ctrl-btn { width: 42px; height: 42px; }
.ctrl-top--fs .ctrl-btn .material-icons { font-size: 24px; }

@media (hover: hover) {
  .ctrl-btn:hover { background: rgba(255,255,255,0.15); }
  .ctrl-btn--danger:hover { background: rgba(244,67,54,0.25); color: #f44336; }
}

/* --- Bottom controls --- */
.ctrl-bottom {
  position: absolute;
  bottom: 0; left: 0; right: 0;
  display: flex;
  justify-content: center;
  padding: 18px 8px 10px;
  background: linear-gradient(0deg, rgba(0,0,0,0.4) 0%, transparent 100%);
  pointer-events: none;
  z-index: 4;
}

.cam-dots {
  display: flex;
  gap: 8px;
  pointer-events: auto;
}

.cam-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: rgba(255,255,255,0.35);
  cursor: pointer;
  transition: all 0.25s ease;
}
.cam-dot.active {
  background: #fff;
  transform: scale(1.4);
  box-shadow: 0 0 6px rgba(255,255,255,0.4);
}
.cam-dot:not(.active):active { background: rgba(255,255,255,0.7); }

@media (hover: hover) {
  .cam-dot:not(.active):hover { background: rgba(255,255,255,0.65); }
}

/* --- Overlays --- */
.stream-overlay {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.88);
  color: #aaa; font-size: 0.9rem;
  z-index: 5;
}

/* --- Fullscreen --- */
.video-viewport:fullscreen,
.video-viewport:-webkit-full-screen {
  background: #000;
  display: flex; align-items: center; justify-content: center;
  border-radius: 0;
}
.video-viewport:fullscreen .video-track,
.video-viewport:-webkit-full-screen .video-track {
  display: flex; align-items: center; justify-content: center;
  width: 100%; height: 100%;
}
.video-viewport:fullscreen .video-el,
.video-viewport:-webkit-full-screen .video-el {
  max-width: 100%; max-height: 100%;
  width: auto; height: auto;
}
</style>
