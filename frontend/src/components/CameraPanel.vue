<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
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
const videoContainerRef = ref(null)

let ws = null
let mediaSource = null
let sourceBuffer = null
let appendQueue = []
let streamingStarted = false
let liveEdgeTimer = null
let gopDecode = null

const cameraOptions = computed(() =>
  cameras.value.map(c => ({ label: c.name || c.id, value: c.id }))
)

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
      if (cameras.value.length > 0) selectedCamera.value = cameras.value[0].id
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
    if (cameras.value.length > 0) selectedCamera.value = cameras.value[0].id
    fetch('/api/ipeye/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getToken()}` },
      body: JSON.stringify({ login: ipeyeLogin.value, password: ipeyePassword.value })
    }).catch(() => {})
  } catch { loginError.value = 'Сервис видеонаблюдения недоступен' }
  finally { loginLoading.value = false }
}

function toggleStream() { streaming.value ? stopStream() : startStream() }

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
    console.log('[camera] codec:', codecStr, '→', mimeType)
    sourceBuffer = mediaSource.addSourceBuffer(mimeType)
    sourceBuffer.mode = 'segments'
    mediaSource.duration = Infinity
    sourceBuffer.addEventListener('updateend', onUpdateEnd)
    sourceBuffer.addEventListener('error', () => {
      streamError.value = 'Ошибка декодирования видео'
    })
  }

  console.log('[camera] connecting to', wsUrl)
  ws = new WebSocket(wsUrl)
  ws.binaryType = 'arraybuffer'

  ws.onopen = () => {
    console.log('[camera] ws connected')
  }

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      try {
        const msg = JSON.parse(event.data)
        if (msg.error) {
          streamError.value = msg.error
          stopStream()
          return
        }
        if (msg.status === 'connected') {
          streamStatus.value = 'Ожидание видеоданных...'
        }
      } catch {
        console.log('[camera] text:', event.data)
      }
      return
    }

    const data = new Uint8Array(event.data)

    if (data[0] === 6) {
      const codecStr = new TextDecoder().decode(data.slice(1))
      console.log('[camera] received codec:', codecStr)
      if (sourceOpen) {
        createSourceBuffer(codecStr)
      } else {
        pendingCodec = codecStr
      }
      return
    }

    if (!sourceBuffer) return

    pushPacket(event.data)

    if (video.paused) {
      video.play().catch(() => {})
    }
  }

  ws.onclose = (e) => {
    console.log('[camera] ws closed, code:', e.code)
    streaming.value = false
    streamStatus.value = ''
    cleanupMse()
  }

  ws.onerror = () => {
    console.error('[camera] ws error')
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
    } catch (err) {
      console.error('[camera] appendBuffer error:', err.message)
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
    try {
      sourceBuffer.appendBuffer(appendQueue.shift())
    } catch (err) {
      console.error('[camera] appendBuffer error:', err.message)
      stopStream()
    }
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

    // Trim old buffer
    const start = sourceBuffer.buffered.start(0)
    if (bufferedEnd - start > 30) {
      try {
        sourceBuffer.remove(start, bufferedEnd - 10)
        return
      } catch {}
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
    if (end - video.currentTime > avgBuf * 3) {
      video.currentTime = end - avgBuf * 1.5
    }
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

function enterFullscreen() {
  const container = videoContainerRef.value
  if (!container) return
  isFullscreen.value = true
  const fn = container.requestFullscreen || container.webkitRequestFullscreen
  if (fn) fn.call(container)
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
  ipeyeSession.value = null; cameras.value = []
  ipeyeLogin.value = ''; ipeyePassword.value = ''
  fetch('/api/ipeye/forget', { method: 'DELETE', headers: { 'Authorization': `Bearer ${getToken()}` } }).catch(() => {})
}
</script>

<template>
  <div class="camera-panel">
    <div v-if="autoConnecting" class="text-center q-pa-xl">
      <q-spinner color="primary" size="2em" />
      <div class="q-mt-sm text-grey-6 text-caption">Подключение к камерам...</div>
    </div>

    <q-card v-else-if="!ipeyeSession" dark class="section-card q-mb-sm">
      <q-card-section>
        <div class="section-title">Видеонаблюдение — Авторизация</div>
        <q-input v-model="ipeyeLogin" filled dense dark label="Логин IPeye" class="q-mb-sm" />
        <q-input v-model="ipeyePassword" filled dense dark label="Пароль" type="password" class="q-mb-sm" />
        <q-btn label="Подключить" color="primary" no-caps :loading="loginLoading" @click="doIpeyeLogin" />
        <div v-if="loginError" class="text-negative q-mt-sm text-caption">{{ loginError }}</div>
      </q-card-section>
    </q-card>

    <template v-if="ipeyeSession">
      <q-card dark class="section-card q-mb-sm">
        <q-card-section class="q-pa-sm">
          <div class="row items-center justify-between q-mb-sm">
            <div class="section-title" style="margin-bottom:0;">Камеры</div>
            <q-btn flat dense size="sm" color="grey-5" icon="logout" @click="doIpeyeLogout" />
          </div>
          <q-select v-model="selectedCamera" :options="cameraOptions" emit-value map-options filled dense dark label="Камера" class="q-mb-sm" :disable="streaming" />
          <div class="row items-center justify-end">
            <q-btn :label="streaming ? 'Остановить' : 'Смотреть'" :color="streaming ? 'negative' : 'primary'" :icon="streaming ? 'stop' : 'play_arrow'" no-caps @click="toggleStream" :disable="!selectedCamera" />
          </div>
        </q-card-section>
      </q-card>

      <q-card v-if="streaming || streamError" dark class="section-card">
        <q-card-section class="q-pa-none">
          <div v-if="streamError" class="text-negative text-center q-pa-md text-caption">{{ streamError }}</div>
          <div ref="videoContainerRef" class="video-container">
            <video ref="videoRef" autoplay muted playsinline class="video-element" />
            <div v-if="streamStatus" class="stream-status-overlay">
              <q-spinner color="primary" size="2em" />
              <div class="q-mt-sm">{{ streamStatus }}</div>
            </div>
            <button v-if="!isFullscreen && !streamStatus" class="fs-enter-btn" @click="enterFullscreen">
              <span class="material-icons">fullscreen</span>
            </button>
            <button v-if="isFullscreen" class="fs-exit-btn" @click="exitFullscreen">
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

.video-container { position: relative; background: #000; border-radius: 0 0 8px 8px; line-height: 0; min-height: 200px; }
.video-element { display: block; width: 100%; height: auto; border-radius: 0 0 8px 8px; }

.stream-status-overlay {
  position: absolute; inset: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.85); color: #aaa; font-size: 0.9rem;
  border-radius: 0 0 8px 8px;
}

.fs-enter-btn {
  position: absolute; top: 8px; right: 8px;
  width: 36px; height: 36px; border-radius: 50%; border: none; cursor: pointer;
  background: rgba(0,0,0,0.5); color: #fff;
  display: flex; align-items: center; justify-content: center;
  opacity: 0; transition: opacity 0.2s;
}
.video-container:hover .fs-enter-btn { opacity: 1; }
@media (pointer: coarse) { .fs-enter-btn { opacity: 0.7; } }

.fs-exit-btn {
  position: absolute; top: 16px; right: 16px; z-index: 10;
  width: 44px; height: 44px; border-radius: 50%; border: none; cursor: pointer;
  background: rgba(255,255,255,0.15); color: #fff;
  display: flex; align-items: center; justify-content: center;
}
.fs-exit-btn .material-icons { font-size: 28px; }

.video-container:fullscreen,
.video-container:-webkit-full-screen {
  background: #000;
  display: flex; align-items: center; justify-content: center;
  border-radius: 0;
}
.video-container:fullscreen .video-element,
.video-container:-webkit-full-screen .video-element {
  max-width: 100%; max-height: 100%;
  width: auto; height: auto;
  border-radius: 0;
}
</style>
