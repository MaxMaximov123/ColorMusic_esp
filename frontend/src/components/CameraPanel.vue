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

const canvasRef = ref(null)
const videoContainerRef = ref(null)
let ws = null

const cameraOptions = computed(() =>
  cameras.value.map(c => ({
    label: c.name || c.id,
    value: c.id
  }))
)

function onFullscreenChange() {
  isFullscreen.value = !!(document.fullscreenElement || document.webkitFullscreenElement)
}

onMounted(() => {
  document.addEventListener('fullscreenchange', onFullscreenChange)
  document.addEventListener('webkitfullscreenchange', onFullscreenChange)
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
    const resp = await fetch('/api/detector/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getToken()}`
      },
      body: JSON.stringify({
        login: ipeyeLogin.value,
        password: ipeyePassword.value
      })
    })

    const data = await resp.json()

    if (!resp.ok) {
      loginError.value = data.error || 'Ошибка авторизации'
      return
    }

    ipeyeSession.value = data.session
    cameras.value = data.cameras || []
    if (cameras.value.length > 0) {
      selectedCamera.value = cameras.value[0].id
    }
  } catch (e) {
    loginError.value = 'Сервис видеонаблюдения недоступен'
  } finally {
    loginLoading.value = false
  }
}

function toggleStream() {
  if (streaming.value) {
    stopStream()
  } else {
    startStream()
  }
}

function startStream() {
  if (!selectedCamera.value || !ipeyeSession.value) return

  streamError.value = ''
  streamStatus.value = 'Подключение...'

  const token = getToken()
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${location.host}/ws/camera?token=${encodeURIComponent(token)}`

  ws = new WebSocket(wsUrl)

  ws.onopen = () => {
    ws.send(JSON.stringify({
      session: ipeyeSession.value,
      camera: selectedCamera.value
    }))
    streaming.value = true
  }

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      try {
        const msg = JSON.parse(event.data)
        if (msg.status) {
          streamStatus.value = msg.status === 'connecting' ? 'Подключение к камере...' : msg.status
        }
        if (msg.error) {
          streamError.value = msg.error
          streamStatus.value = ''
        }
      } catch (e) {
        // ignore
      }
    } else {
      streamStatus.value = ''
      renderFrame(event.data)
    }
  }

  ws.onclose = () => {
    streaming.value = false
    streamStatus.value = ''
  }

  ws.onerror = () => {
    streamError.value = 'Ошибка соединения'
    streaming.value = false
  }
}

function stopStream() {
  if (ws) {
    ws.close()
    ws = null
  }
  streaming.value = false
  streamStatus.value = ''
}

function renderFrame(blob) {
  const canvas = canvasRef.value
  if (!canvas) return

  const url = URL.createObjectURL(new Blob([blob], { type: 'image/jpeg' }))
  const img = new Image()
  img.onload = () => {
    const ctx = canvas.getContext('2d')
    canvas.width = img.width
    canvas.height = img.height
    ctx.drawImage(img, 0, 0)
    URL.revokeObjectURL(url)
  }
  img.src = url
}

function toggleFullscreen() {
  const el = videoContainerRef.value
  if (!el) return
  if (document.fullscreenElement || document.webkitFullscreenElement) {
    ;(document.exitFullscreen || document.webkitExitFullscreen).call(document)
  } else {
    ;(el.requestFullscreen || el.webkitRequestFullscreen).call(el)
  }
}

function doIpeyeLogout() {
  stopStream()
  ipeyeSession.value = null
  cameras.value = []
  ipeyeLogin.value = ''
  ipeyePassword.value = ''
}
</script>

<template>
  <div class="camera-panel">
    <!-- IPeye Auth -->
    <q-card v-if="!ipeyeSession" dark class="section-card q-mb-sm">
      <q-card-section>
        <div class="section-title">Видеонаблюдение — Авторизация</div>
        <q-input
          v-model="ipeyeLogin"
          filled dense dark
          label="Логин IPeye"
          class="q-mb-sm"
        />
        <q-input
          v-model="ipeyePassword"
          filled dense dark
          label="Пароль"
          type="password"
          class="q-mb-sm"
        />
        <q-btn
          label="Подключить"
          color="primary"
          no-caps
          :loading="loginLoading"
          @click="doIpeyeLogin"
        />
        <div v-if="loginError" class="text-negative q-mt-sm text-caption">
          {{ loginError }}
        </div>
      </q-card-section>
    </q-card>

    <!-- Controls -->
    <template v-if="ipeyeSession">
      <q-card dark class="section-card q-mb-sm">
        <q-card-section class="q-pa-sm">
          <div class="row items-center justify-between q-mb-sm">
            <div class="section-title" style="margin-bottom: 0;">Камеры</div>
            <q-btn flat dense icon="logout" size="sm" color="grey-5" @click="doIpeyeLogout" />
          </div>

          <q-select
            v-model="selectedCamera"
            :options="cameraOptions"
            emit-value
            map-options
            filled dense dark
            label="Камера"
            class="q-mb-sm"
            :disable="streaming"
          />

          <div class="row items-center justify-end">
            <q-btn
              :label="streaming ? 'Остановить' : 'Смотреть'"
              :color="streaming ? 'negative' : 'primary'"
              :icon="streaming ? 'stop' : 'play_arrow'"
              no-caps
              @click="toggleStream"
              :disable="!selectedCamera"
            />
          </div>
        </q-card-section>
      </q-card>

      <!-- Video -->
      <q-card v-if="streaming || streamError" dark class="section-card">
        <q-card-section class="q-pa-none">
          <div v-if="streamStatus" class="stream-status text-center q-pa-lg">
            <q-spinner color="primary" size="2em" class="q-mr-sm" />
            {{ streamStatus }}
          </div>
          <div v-if="streamError" class="text-negative text-center q-pa-md text-caption">
            {{ streamError }}
          </div>
          <div
            ref="videoContainerRef"
            class="video-container"
            :class="{ hidden: !!streamStatus && !streamError }"
          >
            <canvas ref="canvasRef" class="video-canvas" />
            <q-btn
              flat round dense
              :icon="isFullscreen ? 'fullscreen_exit' : 'fullscreen'"
              class="fullscreen-btn"
              @click="toggleFullscreen"
            />
          </div>
        </q-card-section>
      </q-card>
    </template>
  </div>
</template>

<style scoped>
.section-card {
  background: #1a1a2e !important;
  border-radius: 8px;
}

.section-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: #aaa;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 10px;
}

.video-container {
  position: relative;
  background: #000;
  border-radius: 0 0 8px 8px;
  line-height: 0;
}

.video-canvas {
  display: block;
  width: 100%;
  height: auto;
  border-radius: 0 0 8px 8px;
}

.video-container.hidden {
  display: none;
}

.fullscreen-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  background: rgba(0, 0, 0, 0.5) !important;
  color: white !important;
  opacity: 0;
  transition: opacity 0.2s;
}

.video-container:hover .fullscreen-btn {
  opacity: 1;
}

.video-container:fullscreen,
.video-container:-webkit-full-screen {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #000;
}

.video-container:fullscreen .video-canvas,
.video-container:-webkit-full-screen .video-canvas {
  width: auto;
  height: auto;
  max-width: 100vw;
  max-height: 100vh;
  border-radius: 0;
}

.video-container:fullscreen .fullscreen-btn,
.video-container:-webkit-full-screen .fullscreen-btn {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 10;
  opacity: 0;
}

.video-container:fullscreen:hover .fullscreen-btn,
.video-container:-webkit-full-screen:hover .fullscreen-btn {
  opacity: 1;
}

.stream-status {
  color: #aaa;
  font-size: 0.9rem;
}
</style>
