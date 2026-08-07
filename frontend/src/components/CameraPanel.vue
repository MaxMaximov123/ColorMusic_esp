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

// Streaming box parser state
let streamBuf = new Uint8Array(0)
let parserPhase = 'init' // 'init' | 'streaming'
let initParts = []
let moofParts = []
let boxCount = 0

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
  streaming.value = true

  try {
    const resp = await fetch(
      `/api/ipeye/authorize/${selectedCamera.value}?session=${encodeURIComponent(ipeyeSession.value)}`,
      { headers: { 'Authorization': `Bearer ${getToken()}` } }
    )
    const data = await resp.json()
    if (!resp.ok) {
      streamError.value = data.error || 'Ошибка авторизации потока'
      streamStatus.value = ''
      streaming.value = false
      return
    }

    streamStatus.value = 'Подключение к камере...'
    connectToStream(data.wsUrl)
  } catch (err) {
    streamError.value = 'Не удалось авторизовать поток'
    streamStatus.value = ''
    streaming.value = false
  }
}

function connectToStream(wsUrl) {
  initSegment = null
  mediaSource = null
  sourceBuffer = null
  appendQueue = []
  hasPlayStarted = false
  streamBuf = new Uint8Array(0)
  parserPhase = 'init'
  initParts = []
  moofParts = []
  boxCount = 0

  console.log('[camera] connecting to', wsUrl)
  ws = new WebSocket(wsUrl)
  ws.binaryType = 'arraybuffer'

  ws.onopen = () => {
    console.log('[camera] ws connected')
    streamStatus.value = 'Ожидание видеоданных...'
  }

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      console.log('[camera] text msg:', event.data)
      return
    }
    feedParser(new Uint8Array(event.data))
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

// ── Streaming fMP4 box parser ──

function readU32(data, off) {
  return ((data[off] << 24) | (data[off + 1] << 16) | (data[off + 2] << 8) | data[off + 3]) >>> 0
}

function boxType(data, off) {
  return String.fromCharCode(data[off + 4], data[off + 5], data[off + 6], data[off + 7])
}

function feedParser(incoming) {
  // Append to stream buffer
  if (streamBuf.length === 0) {
    streamBuf = incoming
  } else {
    const combined = new Uint8Array(streamBuf.length + incoming.length)
    combined.set(streamBuf)
    combined.set(incoming, streamBuf.length)
    streamBuf = combined
  }

  // Parse complete boxes
  let offset = 0
  while (offset + 8 <= streamBuf.length) {
    const size = readU32(streamBuf, offset)

    // Extended size (64-bit) — read lower 32 bits
    if (size === 1 && offset + 16 <= streamBuf.length) {
      const extSize = readU32(streamBuf, offset + 12)
      if (offset + extSize > streamBuf.length) break
      const box = streamBuf.slice(offset, offset + extSize)
      processBox(box)
      offset += extSize
      continue
    }

    if (size < 8) {
      // Invalid: skip one byte and resync
      console.warn('[camera] invalid box size', size, 'at offset', offset)
      offset++
      continue
    }

    if (offset + size > streamBuf.length) break // incomplete box, wait

    const box = streamBuf.slice(offset, offset + size)
    processBox(box)
    offset += size
  }

  // Keep leftover
  if (offset > 0) {
    streamBuf = offset < streamBuf.length ? streamBuf.slice(offset) : new Uint8Array(0)
  }
}

function processBox(box) {
  const type = boxType(box, 0)
  boxCount++

  if (boxCount <= 10) {
    console.log(`[camera] box#${boxCount}: type=${type} size=${box.length}`)
  }

  if (parserPhase === 'init') {
    // Collect ftyp + optional (styp, free, skip, sidx) + moov
    if (type === 'ftyp' || type === 'styp' || type === 'free' || type === 'skip' || type === 'sidx') {
      if (type === 'ftyp' || initParts.length > 0) {
        initParts.push(box)
      }
      return
    }
    if (type === 'moov') {
      if (initParts.length === 0) {
        // moov without ftyp — create minimal ftyp
        console.warn('[camera] moov received without ftyp, using moov alone')
      }
      initParts.push(box)
      // Assemble init segment
      const total = initParts.reduce((s, b) => s + b.length, 0)
      initSegment = new Uint8Array(total)
      let off = 0
      for (const part of initParts) {
        initSegment.set(part, off)
        off += part.length
      }
      initParts = []
      parserPhase = 'streaming'
      console.log('[camera] init segment ready:', total, 'bytes')
      initMse()
      return
    }
    // Unknown box during init — skip
    if (boxCount <= 10) console.log('[camera] skipping box during init:', type)
    return
  }

  // Streaming phase: assemble moof+mdat media segments
  if (type === 'moof') {
    moofParts = [box]
    return
  }
  if (type === 'mdat') {
    if (moofParts.length > 0) {
      moofParts.push(box)
      const total = moofParts.reduce((s, b) => s + b.length, 0)
      const segment = new Uint8Array(total)
      let off = 0
      for (const part of moofParts) {
        segment.set(part, off)
        off += part.length
      }
      moofParts = []
      appendToSourceBuffer(segment)
    }
    return
  }
  // styp between segments is OK, sidx/free/skip — ignore
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
