<script setup>
import { ref, computed, watch } from 'vue'
import { useWebSocket } from './composables/useWebSocket.js'
import DevicePanel from './components/DevicePanel.vue'

const { devices, connected, calibrationResult, sendCommand, requestCalibrate } = useWebSocket()

const selectedDeviceId = ref(null)

const selectedDevice = computed(() => {
  if (!selectedDeviceId.value) return null
  return devices.value.find(d => d.id === selectedDeviceId.value) || null
})

watch(devices, (list) => {
  if (list.length > 0 && !selectedDeviceId.value) {
    selectedDeviceId.value = list[0].id
  }
  if (selectedDeviceId.value && !list.find(d => d.id === selectedDeviceId.value)) {
    selectedDeviceId.value = list.length > 0 ? list[0].id : null
  }
}, { deep: true })

function selectDevice(id) {
  selectedDeviceId.value = id
}
</script>

<template>
  <div class="app">
    <header class="header">
      <h1 class="title">ColorMusic</h1>
      <div class="status" :class="{ online: connected, offline: !connected }">
        <span class="status-dot"></span>
        <span class="status-text">{{ connected ? 'Подключено' : 'Нет связи' }}</span>
      </div>
    </header>

    <div v-if="devices.length === 0" class="no-devices">
      <div class="no-devices-icon">&#9898;</div>
      <p>Нет подключённых устройств</p>
    </div>

    <template v-else>
      <div v-if="devices.length > 1" class="device-tabs">
        <button
          v-for="dev in devices"
          :key="dev.id"
          class="device-tab"
          :class="{ active: selectedDeviceId === dev.id, offline: !dev.online }"
          @click="selectDevice(dev.id)"
        >
          <span class="tab-dot" :class="{ on: dev.online }"></span>
          {{ dev.name || dev.id }}
        </button>
      </div>

      <DevicePanel
        v-if="selectedDevice"
        :device="selectedDevice"
        :calibration-result="calibrationResult"
        @command="(params) => sendCommand(selectedDevice.id, params)"
        @calibrate="requestCalibrate(selectedDevice.id)"
      />
    </template>
  </div>
</template>

<style scoped>
.app {
  max-width: 520px;
  margin: 0 auto;
  padding: 12px;
  padding-bottom: 32px;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.title {
  font-size: 1.5rem;
  font-weight: 700;
  color: #2ee8b7;
}

.status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8rem;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.status.online .status-dot {
  background: #4caf50;
  box-shadow: 0 0 6px #4caf50;
}

.status.offline .status-dot {
  background: #f44336;
  box-shadow: 0 0 6px #f44336;
}

.status-text {
  color: #aaa;
}

.no-devices {
  text-align: center;
  padding: 60px 20px;
  color: #666;
}

.no-devices-icon {
  font-size: 2.5rem;
  margin-bottom: 12px;
  opacity: 0.5;
}

.no-devices p {
  font-size: 1rem;
}

.device-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.device-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 20px;
  background: #1a1a2e;
  color: #aaa;
  font-size: 0.85rem;
  white-space: nowrap;
  transition: all 0.2s;
  border: 1px solid transparent;
}

.device-tab.active {
  color: #2ee8b7;
  border-color: #2ee8b7;
  background: rgba(46, 232, 183, 0.1);
}

.device-tab.offline {
  opacity: 0.5;
}

.tab-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #f44336;
}

.tab-dot.on {
  background: #4caf50;
}
</style>
