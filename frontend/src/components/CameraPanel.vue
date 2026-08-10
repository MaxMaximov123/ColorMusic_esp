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

const videoRef = ref(null)
const viewportRef = ref(null)

let ws = null
let mediaSource = null
let sourceBuffer = null
let appendQueue = []
let streamingStarted = false
let liveEdgeTimer = null
let gopDecode = null

// Zoom state (transform-origin: 0 0; transform: translate(px,py) scale(S))
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

// Swipe state
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
  try {
    const resp = await fetch('/api/ipeye/connect', {
      headers: { 'Authorization': `Bearer ${getToken()}` }
    })
    const data = await resp.json()
    if (data.session) {
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
  if (!selectedCamera.value || !ipeyeSession.value) return
  streamError.value = ''
  streamStatus.value = 'Подключение...'
  streaming.value = true

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${location.host}/ws/camera?session=${encodeURIComponent(ipeyeSession.value)}&camera=${encodeURIComponent(selectedCamera.value)}&token=${encodeURIComponent(getToken())}`
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
  mediaSource = new MSrc()
  video.src = URL.createObjectURL(mediaSource)

  let sourceOpen = false
  let pendingCodec = null

  mediaSource.addEventListener('sourceopen', () => {
    sourceOpen = true
    if (pendingCodec) createSourceBuffer(pendingCodec)
  })

  function createSourceBuffer(codecStr) {
    if (sourceBuffer) return
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
  }

  ws = new WebSocket(wsUrl)
  ws.binaryType = 'arraybuffer'

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      try {
        const msg = JSON.parse(event.data)
        if (msg.error) { streamError.value = msg.error; stopStream(); return }
        if (msg.status === 'connected') streamStatus.value = 'Ожидание видеоданных...'
      } catch {}
      return
    }

    const data = new Uint8Array(event.data)
    if (data[0] === 6) {
      const codecStr = new TextDecoder().decode(data.slice(1))
      if (sourceOpen) createSourceBuffer(codecStr)
      else pendingCodec = codecStr
      return
    }

    if (!sourceBuffer) return
    pushPacket(event.data)
    if (video.paused) video.play().catch(() => {})
  }

  ws.onclose = () => {
    streaming.value = false
    streamStatus.value = ''
    cleanupMse()
  }

  ws.onerror = () => {
    streamError.value = 'Ошибка соединения с камерой'
    streaming.value = false
    streamStatus.value = ''
    cleanupMse()
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
  if (ws) { ws.close(); ws = null }
  streaming.value = false
  streamStatus.value = ''
  cleanupMse()
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

// --- Touch handlers ---
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
    swipeOffset.value = dx * 0.3
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

  // Swipe to switch camera
  if (zoomScale.value <= 1 && Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 2 && dt < 500) {
    const idx = currentCameraIndex.value
    if (dx < 0 && idx < cameras.value.length - 1) switchCamera(idx + 1)
    else if (dx > 0 && idx > 0) switchCamera(idx - 1)
    return
  }

  // Double-tap to zoom / reset
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

// --- Mouse wheel zoom ---
function onWheel(e) {
  const delta = e.deltaY > 0 ? -0.3 : 0.3
  const rect = viewportRef.value.getBoundingClientRect()
  zoomAtPoint(zoomScale.value + delta, e.clientX - rect.left, e.clientY - rect.top)
}

// --- Mouse drag for panning ---
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

// --- Double-click zoom (desktop) ---
function onDblClick(e) {
  if (zoomScale.value > 1) {
    resetZoom()
  } else {
    const rect = viewportRef.value.getBoundingClientRect()
    zoomAtPoint(2.5, e.clientX - rect.left, e.clientY - rect.top)
  }
}

// --- Fullscreen ---
function enterFullscreen() {
  const el = viewportRef.value
  if (!el) return
  isFullscreen.value = true
  const fn = el.requestFullscreen || el.webkitRequestFullscreen
  if (fn) fn.call(el)
  try { screen.orientation.lock('landscape').catch(() => {}) } catch {}
}

function exitFullscreen() {
  isFullscreen.value = false
  try {
    if (document.fullscreenElement) document.exitFullscreen()
    else if (document.webkitFullscreenElement) document.webkitExitFullscreen()
  } catch {}
  try { screen.orientation.unlock() } catch {}
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
    <div v-if="autoConnecting" class="text-center q-pa-xl">
      <q-spinner color="primary" size="2em" />
      <div class="q-mt-sm text-grey-6 text-caption">Подключение к камерам...</div>
    </div>

    <!-- Login -->
    <q-card v-else-if="!ipeyeSession" dark class="section-card q-mb-sm">
      <q-card-section>
        <div class="section-title">Видеонаблюдение — Авторизация</div>
        <q-input v-model="ipeyeLogin" filled dense dark label="Логин IPeye" class="q-mb-sm" />
        <q-input v-model="ipeyePassword" filled dense dark label="Пароль" type="password" class="q-mb-sm" />
        <q-btn label="Подключить" color="primary" no-caps :loading="loginLoading" @click="doIpeyeLogin" />
        <div v-if="loginError" class="text-negative q-mt-sm text-caption">{{ loginError }}</div>
      </q-card-section>
    </q-card>

    <!-- Camera viewer -->
    <template v-if="ipeyeSession">
      <q-card dark class="section-card camera-card">
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

            <div class="video-wrapper" :style="{ transform: `translateX(${swipeOffset}px)` }">
              <video ref="videoRef" autoplay muted playsinline
                     class="video-element" :style="zoomStyle" />
            </div>

            <!-- Top bar -->
            <div class="camera-top-bar">
              <span class="camera-name">{{ currentCameraName }}</span>
              <button class="icon-btn" @click.stop="doIpeyeLogout" title="Выйти">
                <span class="material-icons" style="font-size:18px">logout</span>
              </button>
            </div>

            <!-- Dot indicators -->
            <div v-if="cameras.length > 1" class="camera-dots">
              <span v-for="(cam, i) in cameras" :key="cam.id"
                    class="dot" :class="{ active: cam.id === selectedCamera }"
                    @click.stop="switchCamera(i)" />
            </div>

            <!-- Status overlay -->
            <div v-if="streamStatus" class="stream-overlay">
              <q-spinner color="primary" size="2em" />
              <div class="q-mt-sm">{{ streamStatus }}</div>
            </div>

            <!-- Error overlay -->
            <div v-if="streamError && !streamStatus" class="stream-overlay">
              <div class="text-negative q-mb-sm">{{ streamError }}</div>
              <q-btn flat dense color="primary" no-caps label="Повторить" @click="startStream" />
            </div>

            <!-- Zoom badge -->
            <div v-if="zoomScale > 1" class="zoom-badge">{{ zoomScale.toFixed(1) }}x</div>

            <!-- Fullscreen -->
            <button v-if="!isFullscreen && !streamStatus" class="icon-btn fs-enter-btn" @click.stop="enterFullscreen">
              <span class="material-icons">fullscreen</span>
            </button>
            <button v-if="isFullscreen" class="icon-btn fs-exit-btn" @click.stop="exitFullscreen">
              <span class="material-icons">close</span>
            </button>
          </div>
        </q-card-section>
      </q-card>
    </template>
  </div>
</template>

<style scoped>
.section-card { background: #1a1a2e !important; border-radius: 8px; }
.section-title { font-size: 0.85rem; font-weight: 600; color: #aaa; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }

.camera-card { overflow: hidden; }

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

.video-wrapper {
  width: 100%;
  transition: transform 0.15s ease-out;
}

.video-element {
  display: block;
  width: 100%;
  height: auto;
}

/* Top bar */
.camera-top-bar {
  position: absolute;
  top: 0; left: 0; right: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px 20px;
  background: linear-gradient(to bottom, rgba(0,0,0,0.6), transparent);
  pointer-events: none;
  z-index: 3;
}

.camera-name {
  color: #fff;
  font-size: 0.85rem;
  font-weight: 500;
  text-shadow: 0 1px 3px rgba(0,0,0,0.8);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.icon-btn {
  width: 34px; height: 34px;
  border-radius: 50%;
  border: none;
  cursor: pointer;
  background: rgba(0,0,0,0.45);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
  transition: background 0.15s;
  flex-shrink: 0;
}
.icon-btn:hover { background: rgba(255,255,255,0.2); }
.icon-btn .material-icons { font-size: 20px; }

/* Dot indicators */
.camera-dots {
  position: absolute;
  bottom: 10px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 7px;
  z-index: 3;
  pointer-events: auto;
}

.dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: rgba(255,255,255,0.4);
  cursor: pointer;
  transition: all 0.2s;
}
.dot.active {
  background: #fff;
  transform: scale(1.3);
  box-shadow: 0 0 4px rgba(255,255,255,0.5);
}
.dot:hover:not(.active) { background: rgba(255,255,255,0.7); }

/* Overlays */
.stream-overlay {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.85);
  color: #aaa; font-size: 0.9rem;
  z-index: 2;
}

/* Zoom badge */
.zoom-badge {
  position: absolute;
  top: 10px; left: 50%;
  transform: translateX(-50%);
  background: rgba(0,0,0,0.6);
  color: #fff;
  font-size: 0.75rem;
  padding: 2px 8px;
  border-radius: 10px;
  z-index: 3;
  pointer-events: none;
}

/* Fullscreen buttons */
.fs-enter-btn {
  position: absolute;
  top: 8px; right: 8px;
  opacity: 0;
  transition: opacity 0.2s;
  z-index: 3;
}
.video-viewport:hover .fs-enter-btn { opacity: 1; }
@media (pointer: coarse) { .fs-enter-btn { opacity: 0.7; } }

.fs-exit-btn {
  position: absolute;
  top: 16px; right: 16px;
  z-index: 10;
  width: 44px; height: 44px;
}
.fs-exit-btn .material-icons { font-size: 28px; }

/* Fullscreen mode */
.video-viewport:fullscreen,
.video-viewport:-webkit-full-screen {
  background: #000;
  display: flex; align-items: center; justify-content: center;
  border-radius: 0;
}
.video-viewport:fullscreen .video-wrapper,
.video-viewport:-webkit-full-screen .video-wrapper {
  display: flex; align-items: center; justify-content: center;
  width: 100%; height: 100%;
}
.video-viewport:fullscreen .video-element,
.video-viewport:-webkit-full-screen .video-element {
  max-width: 100%; max-height: 100%;
  width: auto; height: auto;
}
</style>
