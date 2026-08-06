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
const videoContainerRef = ref(null)

let ws = null
let mediaSource = null
let sourceBuffer = null
let initSegment = null
let pendingBuffers = []
let moofBuffer = null

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

async function startStream() {
  if (!selectedCamera.value || !ipeyeSession.value) return
  streamError.value = ''
  streamStatus.value = 'Авторизация потока...'

  try {
    const resp = await fetch(
      `/api/ipeye/authorize/${selectedCamera.value}?session=${encodeURIComponent(ipeyeSession.value)}`,
      { headers: { 'Authorization': `Bearer ${getToken()}` } }
    )
    const data = await resp.json()
    if (!resp.ok) {
      streamError.value = data.error || 'Ошибка авторизации потока'
      streamStatus.value = ''
      return
    }

    streamStatus.value = 'Подключение к камере...'
    connectToStream(data.wsUrl)
  } catch (err) {
    streamError.value = 'Не удалось авторизовать поток'
    streamStatus.value = ''
  }
}

function connectToStream(wsUrl) {
  initSegment = null
  pendingBuffers = []
  moofBuffer = null
  mediaSource = null
  sourceBuffer = null

  ws = new WebSocket(wsUrl)
  ws.binaryType = 'arraybuffer'

  ws.onopen = () => {
    streaming.value = true
    streamStatus.value = 'Ожидание видеоданных...'
  }

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') return
    handleFmp4Box(new Uint8Array(event.data))
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

function getBoxType(data) {
  if (data.length < 8) return null
  return String.fromCharCode(data[4], data[5], data[6], data[7])
}

function handleFmp4Box(data) {
  const boxType = getBoxType(data)
  if (!boxType) return

  if (!initSegment) {
    if (boxType === 'ftyp' || boxType === 'styp' || boxType === 'moov') {
      if (!pendingBuffers.length && boxType !== 'ftyp') return
      pendingBuffers.push(data)

      if (boxType === 'moov' || (boxType === 'ftyp' && containsBox(data, 'moov'))) {
        const total = pendingBuffers.reduce((s, b) => s + b.length, 0)
        initSegment = new Uint8Array(total)
        let offset = 0
        for (const buf of pendingBuffers) {
          initSegment.set(buf, offset)
          offset += buf.length
        }
        pendingBuffers = []
        initMse()
      }
      return
    }
    return
  }

  if (boxType === 'moof') {
    moofBuffer = data
    return
  }

  if (boxType === 'mdat' && moofBuffer) {
    const segment = new Uint8Array(moofBuffer.length + data.length)
    segment.set(moofBuffer, 0)
    segment.set(data, moofBuffer.length)
    moofBuffer = null
    appendToSourceBuffer(segment)
    return
  }

  if (boxType === 'sidx' || boxType === 'free' || boxType === 'skip' || boxType === 'mfra') {
    return
  }
}

function containsBox(data, type) {
  const target = [type.charCodeAt(0), type.charCodeAt(1), type.charCodeAt(2), type.charCodeAt(3)]
  for (let i = 0; i <= data.length - 8; i++) {
    if (data[i + 4] === target[0] && data[i + 5] === target[1] &&
        data[i + 6] === target[2] && data[i + 7] === target[3]) return true
  }
  return false
}

function initMse() {
  const video = videoRef.value
  if (!video || !initSegment) return

  const mimeType = 'video/mp4; codecs="avc1.42E01E"'
  if (!MediaSource.isTypeSupported(mimeType)) {
    streamError.value = 'Браузер не поддерживает воспроизведение этого формата'
    stopStream()
    return
  }

  mediaSource = new MediaSource()
  video.src = URL.createObjectURL(mediaSource)

  mediaSource.addEventListener('sourceopen', () => {
    try {
      sourceBuffer = mediaSource.addSourceBuffer(mimeType)
      sourceBuffer.mode = 'segments'
      sourceBuffer.addEventListener('updateend', flushQueue)
      appendToSourceBuffer(initSegment)
      streamStatus.value = ''
      video.play().catch(() => {})
    } catch (err) {
      streamError.value = 'Ошибка инициализации видео: ' + err.message
      stopStream()
    }
  })
}

const appendQueue = []
let isAppending = false

function appendToSourceBuffer(data) {
  appendQueue.push(data)
  flushQueue()
}

function flushQueue() {
  if (!sourceBuffer || sourceBuffer.updating || !appendQueue.length) return
  if (mediaSource.readyState !== 'open') return

  isAppending = true
  const data = appendQueue.shift()
  try {
    sourceBuffer.appendBuffer(data)

    if (sourceBuffer.buffered.length > 0) {
      const buffered = sourceBuffer.buffered.end(0) - sourceBuffer.buffered.start(0)
      if (buffered > 30) {
        sourceBuffer.remove(sourceBuffer.buffered.start(0), sourceBuffer.buffered.end(0) - 10)
      }
    }
  } catch (err) {
    if (err.name === 'QuotaExceededError' && sourceBuffer.buffered.length > 0) {
      sourceBuffer.remove(sourceBuffer.buffered.start(0), sourceBuffer.buffered.end(0) - 5)
    }
  }
}

function cleanupMse() {
  appendQueue.length = 0
  isAppending = false
  if (sourceBuffer) {
    try { mediaSource.removeSourceBuffer(sourceBuffer) } catch {}
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
          <div v-if="streamStatus" class="stream-status text-center q-pa-lg">
            <q-spinner color="primary" size="2em" class="q-mr-sm" />{{ streamStatus }}
          </div>
          <div v-if="streamError" class="text-negative text-center q-pa-md text-caption">{{ streamError }}</div>
          <div ref="videoContainerRef" class="video-container" :class="{ hidden: !!streamStatus && !streamError, fullscreen: isFullscreen }">
            <video ref="videoRef" autoplay muted playsinline class="video-element" />
            <button v-if="!isFullscreen" class="fs-enter-btn" @click="enterFullscreen">
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
.stream-status { color: #aaa; font-size: 0.9rem; }

.video-container { position: relative; background: #000; border-radius: 0 0 8px 8px; line-height: 0; }
.video-container.hidden { display: none; }
.video-element { display: block; width: 100%; height: auto; border-radius: 0 0 8px 8px; }

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
