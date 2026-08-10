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
let initSegment = null
let appendQueue = []
let hasPlayStarted = false
let liveEdgeTimer = null

// fMP4 box state (each WS message = one box, like in the Python reference)
let gotMoov = false
let initBufs = []
let moofBuf = null

const VALID_BOXES = new Set(['ftyp', 'styp', 'moov', 'moof', 'mdat', 'sidx', 'free', 'skip', 'mfra'])

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
  initSegment = null
  mediaSource = null
  sourceBuffer = null
  appendQueue = []
  hasPlayStarted = false
  gotMoov = false
  initBufs = []
  moofBuf = null

  console.log('[camera] connecting to', wsUrl)
  ws = new WebSocket(wsUrl)
  ws.binaryType = 'arraybuffer'

  let msgCount = 0
  let byteCount = 0

  ws.onopen = () => {
    console.log('[camera] ws connected')
    streamStatus.value = 'Ожидание видеоданных...'
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
    const msg = new Uint8Array(event.data)
    msgCount++
    byteCount += msg.length

    if (msgCount <= 5) {
      const hex = Array.from(msg.slice(0, 16)).map(b => b.toString(16).padStart(2, '0')).join(' ')
      console.log(`[camera] msg#${msgCount}: ${msg.length} bytes, hex: ${hex}`)
    }

    // Skip tiny messages (keepalive/ping)
    if (msg.length < 8) return

    const type = String.fromCharCode(msg[4], msg[5], msg[6], msg[7])

    // Show diagnostics while waiting for init
    if (!initSegment) {
      streamStatus.value = `${msgCount} msg, ${(byteCount / 1024).toFixed(0)}KB | box=${type} (${msg.length}b)`
    }

    if (!VALID_BOXES.has(type)) {
      if (msgCount <= 10) console.log(`[camera] unknown box type: ${type}`)
      return
    }

    // Phase 1: assemble init segment (ftyp + moov)
    if (!gotMoov) {
      if (type === 'ftyp') {
        initBufs = [msg]
        // ftyp+moov can arrive in a single message
        if (containsSubstring(msg, 'moov')) {
          gotMoov = true
          console.log(`[camera] ftyp+moov in one message: ${msg.length} bytes`)
          assembleInitSegment()
        } else {
          console.log(`[camera] ftyp: ${msg.length} bytes, waiting for moov...`)
        }
        return
      }
      if (type === 'moov') {
        initBufs.push(msg)
        gotMoov = true
        console.log(`[camera] moov: ${msg.length} bytes, total init: ${initBufs.reduce((s, b) => s + b.length, 0)} bytes`)
        assembleInitSegment()
        return
      }
      if (type === 'styp') {
        if (initBufs.length > 0) initBufs.push(msg)
        return
      }
      return
    }

    // Phase 2: media segments (moof+mdat pairs for MSE)
    if (type === 'moof') {
      moofBuf = msg
      return
    }
    if (type === 'mdat' && moofBuf) {
      const segment = new Uint8Array(moofBuf.length + msg.length)
      segment.set(moofBuf, 0)
      segment.set(msg, moofBuf.length)
      moofBuf = null
      appendToSourceBuffer(segment)
      return
    }
    // sidx, free, skip, mfra — ignore
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

function containsSubstring(data, str) {
  const t0 = str.charCodeAt(0), t1 = str.charCodeAt(1), t2 = str.charCodeAt(2), t3 = str.charCodeAt(3)
  for (let i = 0; i <= data.length - 4; i++) {
    if (data[i] === t0 && data[i + 1] === t1 && data[i + 2] === t2 && data[i + 3] === t3) return true
  }
  return false
}

function assembleInitSegment() {
  const total = initBufs.reduce((s, b) => s + b.length, 0)
  initSegment = new Uint8Array(total)
  let off = 0
  for (const buf of initBufs) {
    initSegment.set(buf, off)
    off += buf.length
  }
  initBufs = []
  console.log('[camera] init segment ready:', total, 'bytes')
  initMse()
}

function extractCodec(initData) {
  // Scan for avcC box and extract profile/compat/level → codec string
  const avcC = [0x61, 0x76, 0x63, 0x43] // 'avcC'
  for (let i = 0; i <= initData.length - 12; i++) {
    if (initData[i + 4] === avcC[0] && initData[i + 5] === avcC[1] &&
        initData[i + 6] === avcC[2] && initData[i + 7] === avcC[3]) {
      const profile = initData[i + 9]
      const compat = initData[i + 10]
      const level = initData[i + 11]
      const codec = `avc1.${profile.toString(16).padStart(2, '0')}${compat.toString(16).padStart(2, '0')}${level.toString(16).padStart(2, '0')}`
      console.log('[camera] detected codec:', codec)
      return codec
    }
  }
  // Check for hvcC (H.265)
  const hvcC = [0x68, 0x76, 0x63, 0x43] // 'hvcC'
  for (let i = 0; i <= initData.length - 12; i++) {
    if (initData[i + 4] === hvcC[0] && initData[i + 5] === hvcC[1] &&
        initData[i + 6] === hvcC[2] && initData[i + 7] === hvcC[3]) {
      console.log('[camera] detected HEVC stream')
      return 'hev1.1.6.L93.B0'
    }
  }
  return null
}

function findSupportedMime(preferredCodec) {
  const candidates = preferredCodec ? [preferredCodec] : []
  candidates.push('avc1.640028', 'avc1.4d401f', 'avc1.42E01E', 'avc1.42001e')
  for (const codec of candidates) {
    const mime = `video/mp4; codecs="${codec}"`
    if (MediaSource.isTypeSupported(mime)) return mime
  }
  return null
}

function initMse() {
  const video = videoRef.value
  if (!video || !initSegment) return

  const detectedCodec = extractCodec(initSegment)
  const mimeType = findSupportedMime(detectedCodec)

  if (!mimeType) {
    streamError.value = 'Браузер не поддерживает воспроизведение этого формата'
    stopStream()
    return
  }

  console.log('[camera] using mime:', mimeType)
  mediaSource = new MediaSource()
  video.src = URL.createObjectURL(mediaSource)

  mediaSource.addEventListener('sourceopen', () => {
    try {
      sourceBuffer = mediaSource.addSourceBuffer(mimeType)
      sourceBuffer.mode = 'sequence'
      sourceBuffer.addEventListener('updateend', onUpdateEnd)
      sourceBuffer.addEventListener('error', (e) => {
        console.error('[camera] sourceBuffer error', e)
        streamError.value = 'Ошибка декодирования видео'
      })
      console.log('[camera] appending init segment')
      appendToSourceBuffer(initSegment)
    } catch (err) {
      console.error('[camera] addSourceBuffer failed:', err)
      streamError.value = 'Ошибка инициализации видео: ' + err.message
      stopStream()
    }
  })

  mediaSource.addEventListener('sourceclose', () => {
    console.log('[camera] mediaSource closed')
  })
}

function onUpdateEnd() {
  if (!hasPlayStarted && sourceBuffer && sourceBuffer.buffered.length > 0) {
    hasPlayStarted = true
    streamStatus.value = ''
    const video = videoRef.value
    if (video) {
      console.log('[camera] starting playback')
      video.play().catch(() => {})
      startLiveEdgeSeeker()
    }
  }
  flushQueue()
}

function appendToSourceBuffer(data) {
  appendQueue.push(data)
  flushQueue()
}

function flushQueue() {
  if (!sourceBuffer || sourceBuffer.updating || !appendQueue.length) return
  if (!mediaSource || mediaSource.readyState !== 'open') return

  // Trim old data if buffer too large (before appending)
  if (sourceBuffer.buffered.length > 0) {
    const start = sourceBuffer.buffered.start(0)
    const end = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1)
    if (end - start > 30) {
      try {
        sourceBuffer.remove(start, end - 10)
        return // updateend will call flushQueue again
      } catch {}
    }
  }

  const data = appendQueue.shift()
  try {
    sourceBuffer.appendBuffer(data)
  } catch (err) {
    console.error('[camera] appendBuffer error:', err.name, err.message)
    if (err.name === 'QuotaExceededError' && sourceBuffer.buffered.length > 0) {
      try {
        sourceBuffer.remove(
          sourceBuffer.buffered.start(0),
          sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1) - 5
        )
      } catch {}
    }
  }
}

function startLiveEdgeSeeker() {
  if (liveEdgeTimer) return
  liveEdgeTimer = setInterval(() => {
    const video = videoRef.value
    if (!video || !sourceBuffer || !sourceBuffer.buffered.length) return
    const end = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1)
    if (end - video.currentTime > 3) {
      video.currentTime = end - 0.5
    }
  }, 2000)
}

function cleanupMse() {
  appendQueue = []
  hasPlayStarted = false
  if (liveEdgeTimer) { clearInterval(liveEdgeTimer); liveEdgeTimer = null }
  if (sourceBuffer) {
    try {
      sourceBuffer.removeEventListener('updateend', onUpdateEnd)
      mediaSource.removeSourceBuffer(sourceBuffer)
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
