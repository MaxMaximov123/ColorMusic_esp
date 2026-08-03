<script setup>
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth.js'
import { useWebSocket } from '../composables/useWebSocket.js'
import ColorMusicPanel from '../components/ColorMusicPanel.vue'
import TempSensorPanel from '../components/TempSensorPanel.vue'
import CameraPanel from '../components/CameraPanel.vue'

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
    <q-header class="bg-dark">
      <q-toolbar>
        <q-toolbar-title style="color: #2ee8b7; font-weight: 700;">
          Smart Home
        </q-toolbar-title>

        <div class="status-indicator q-mr-md">
          <span
            class="status-dot"
            :style="{ background: connected ? '#4caf50' : '#f44336', boxShadow: connected ? '0 0 6px #4caf50' : '0 0 6px #f44336' }"
          ></span>
          <span class="status-text">{{ connected ? 'Подключено' : 'Нет связи' }}</span>
        </div>

        <q-btn flat icon="logout" @click="doLogout" color="grey-5" />
      </q-toolbar>

      <q-tabs
        v-model="selectedTab"
        align="left"
        active-color="primary"
        indicator-color="primary"
        narrow-indicator
        class="bg-dark"
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
      <q-page class="q-pa-md" style="max-width: 600px; margin: 0 auto;">
        <q-tab-panels
          v-model="selectedTab"
          animated
          class="bg-transparent"
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
.status-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.status-text {
  font-size: 0.8rem;
  color: #aaa;
}

.device-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}
</style>
