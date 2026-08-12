<script setup>
import { ref, watch, defineAsyncComponent } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth.js'
import { useWebSocket } from '../composables/useWebSocket.js'

const ColorMusicPanel = defineAsyncComponent(() => import('../components/ColorMusicPanel.vue'))
const TempSensorPanel = defineAsyncComponent(() => import('../components/TempSensorPanel.vue'))
const CameraPanel = defineAsyncComponent(() => import('../components/CameraPanel.vue'))

const router = useRouter()
const { logout } = useAuth()
const { devices, connected, calibrationResult, sendCommand, requestCalibrate } = useWebSocket()

const selectedTab = ref('cameras')
let autoSelected = false

watch(devices, (list) => {
  if (!autoSelected && list.length > 0) {
    selectedTab.value = list[0].id
    autoSelected = true
  }
  if (selectedTab.value && selectedTab.value !== 'cameras' && !list.find(d => d.id === selectedTab.value)) {
    selectedTab.value = list.length > 0 ? list[0].id : 'cameras'
  }
}, { deep: true })

function doLogout() {
  logout()
  router.push('/login')
}
</script>

<template>
  <q-layout view="hHh lpr fFf">
    <q-header class="bg-dark header-safe">
      <q-toolbar class="toolbar-compact">
        <q-toolbar-title class="app-title">
          Smart Home
        </q-toolbar-title>

        <div class="status-indicator">
          <span
            class="status-dot"
            :style="{ background: connected ? '#4caf50' : '#f44336', boxShadow: connected ? '0 0 6px #4caf50' : '0 0 6px #f44336' }"
          ></span>
          <span class="status-text hide-xs">{{ connected ? 'Подключено' : 'Нет связи' }}</span>
        </div>

        <q-btn flat icon="logout" @click="doLogout" color="grey-5" size="sm" padding="xs" />
      </q-toolbar>

      <q-tabs
        v-model="selectedTab"
        align="left"
        active-color="primary"
        indicator-color="primary"
        narrow-indicator
        class="bg-dark tabs-compact"
        dense
      >
        <q-tab
          v-for="dev in devices"
          :key="dev.id"
          :name="dev.id"
          no-caps
        >
          <div class="row items-center no-wrap q-gutter-x-xs">
            <span
              class="device-dot"
              :style="{ background: dev.online ? '#4caf50' : '#888' }"
            ></span>
            <span>{{ dev.name || dev.id }}</span>
          </div>
        </q-tab>
        <q-tab name="cameras" no-caps icon="videocam" label="Камеры" />
      </q-tabs>
    </q-header>

    <q-page-container>
      <q-page class="page-content">
        <q-tab-panels
          v-model="selectedTab"
          animated
          class="bg-transparent"
          keep-alive-exclude="CameraPanel"
        >
          <q-tab-panel
            v-for="dev in devices"
            :key="dev.id"
            :name="dev.id"
            class="q-pa-none"
          >
            <ColorMusicPanel
              v-if="dev.deviceType === 'colormusic'"
              :device="dev"
              :calibration-result="calibrationResult"
              @command="(params) => sendCommand(dev.id, params)"
              @calibrate="requestCalibrate(dev.id)"
            />
            <TempSensorPanel
              v-else-if="dev.deviceType === 'tempsensor'"
              :device="dev"
              @command="(params) => sendCommand(dev.id, params)"
            />
            <div v-else class="text-grey-6 text-center q-pa-lg">
              Неизвестный тип устройства: {{ dev.deviceType }}
            </div>
          </q-tab-panel>

          <q-tab-panel name="cameras" class="q-pa-none">
            <CameraPanel />
          </q-tab-panel>
        </q-tab-panels>
      </q-page>
    </q-page-container>
  </q-layout>
</template>

<style scoped>
.header-safe {
  padding-top: env(safe-area-inset-top, 0);
  padding-left: env(safe-area-inset-left, 0);
  padding-right: env(safe-area-inset-right, 0);
}

.toolbar-compact {
  min-height: 44px;
  padding: 0 8px;
}

.app-title {
  color: #2ee8b7;
  font-weight: 700;
  font-size: 1.1rem;
}

.tabs-compact {
  min-height: 36px;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 8px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}

.status-text {
  font-size: 0.75rem;
  color: #aaa;
}

.device-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}

.page-content {
  max-width: 600px;
  margin: 0 auto;
  padding: 8px;
  padding-bottom: calc(8px + env(safe-area-inset-bottom, 0));
}

@media (min-width: 768px) {
  .page-content {
    max-width: 800px;
    padding: 16px;
  }
  .toolbar-compact {
    min-height: 48px;
    padding: 0 12px;
  }
  .app-title {
    font-size: 1.2rem;
  }
}

@media (min-width: 1200px) {
  .page-content {
    max-width: 960px;
  }
}

@media (max-width: 359px) {
  .hide-xs {
    display: none;
  }
}
</style>
