<script setup>
import { computed, ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Filler,
  Tooltip
} from 'chart.js'
import { useApi } from '../composables/useApi.js'

ChartJS.register(LineElement, PointElement, LinearScale, CategoryScale, Filler, Tooltip)

const props = defineProps({
  device: { type: Object, required: true }
})
const emit = defineEmits(['command'])
const { fetchTemperatureHistory } = useApi()

const state = computed(() => props.device.state || {})
const temp = computed(() => state.value.temp ?? null)
const threshold = computed(() => state.value.threshold ?? 0)
const notify = computed(() => !!(state.value.notify))
const online = computed(() => props.device.online)

const thresholdInput = ref(threshold.value)
const chartFullscreen = ref(false)
const chartWrapRef = ref(null)

watch(threshold, (val) => { thresholdInput.value = val })

function onThresholdChange(val) {
  const num = parseFloat(val)
  if (!isNaN(num)) emit('command', { threshold: num })
}

function onNotifyToggle(val) {
  emit('command', { notify: val ? 1 : 0 })
}

const hoursOptions = [
  { label: '6ч', value: 6 },
  { label: '24ч', value: 24 },
  { label: '7д', value: 168 }
]

const selectedHours = ref(24)
const historyData = ref([])
const chartLoading = ref(false)
let refreshTimer = null

const chartData = computed(() => {
  const labels = historyData.value.map(p => {
    const d = new Date(p.recorded_at)
    if (selectedHours.value <= 24) {
      return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    }
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) + ' ' +
           d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  })
  return {
    labels,
    datasets: [{
      label: 'Температура',
      data: historyData.value.map(p => p.temperature),
      borderColor: '#2ee8b7',
      backgroundColor: 'rgba(46, 232, 183, 0.1)',
      fill: true,
      tension: 0.4,
      pointRadius: 0,
      pointHitRadius: 10,
      borderWidth: 2
    }]
  }
})

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  plugins: {
    legend: { display: false },
    tooltip: {
      mode: 'index', intersect: false,
      callbacks: { label: (ctx) => `${ctx.parsed.y.toFixed(1)}°C` }
    }
  },
  scales: {
    x: {
      ticks: { color: '#888', maxTicksLimit: chartFullscreen.value ? 16 : 8, font: { size: chartFullscreen.value ? 12 : 10 } },
      grid: { color: 'rgba(255,255,255,0.06)' }
    },
    y: {
      ticks: { color: '#888', font: { size: chartFullscreen.value ? 13 : 11 }, callback: (v) => v + '°C' },
      grid: { color: 'rgba(255,255,255,0.06)' }
    }
  },
  interaction: { mode: 'nearest', axis: 'x', intersect: false }
}))

async function loadHistory() {
  chartLoading.value = true
  try {
    historyData.value = await fetchTemperatureHistory(props.device.id, selectedHours.value)
  } catch (e) {
    console.error('Failed to load temperature history:', e)
  } finally {
    chartLoading.value = false
  }
}

watch(selectedHours, () => loadHistory())

function onFullscreenChange() {
  if (!document.fullscreenElement && !document.webkitFullscreenElement) {
    chartFullscreen.value = false
    document.body.style.overflow = ''
    try { screen.orientation.unlock() } catch {}
  }
}

async function enterChartFullscreen() {
  chartFullscreen.value = true
  document.body.style.overflow = 'hidden'
  await nextTick()
  const el = chartWrapRef.value
  if (el) {
    try { (el.requestFullscreen || el.webkitRequestFullscreen).call(el) } catch {}
  }
  try { screen.orientation.lock('landscape').catch(() => {}) } catch {}
}

function exitChartFullscreen() {
  chartFullscreen.value = false
  document.body.style.overflow = ''
  try {
    if (document.fullscreenElement) document.exitFullscreen()
    else if (document.webkitFullscreenElement) document.webkitExitFullscreen()
  } catch {}
  try { screen.orientation.unlock() } catch {}
}

onMounted(() => {
  loadHistory()
  refreshTimer = setInterval(loadHistory, 60000)
  document.addEventListener('fullscreenchange', onFullscreenChange)
  document.addEventListener('webkitfullscreenchange', onFullscreenChange)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  if (chartFullscreen.value) exitChartFullscreen()
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.removeEventListener('webkitfullscreenchange', onFullscreenChange)
})
</script>

<template>
  <div class="temp-sensor-panel">
    <q-card dark class="section-card q-mb-sm">
      <q-card-section class="text-center q-pa-lg">
        <div class="temp-display" :class="{ offline: !online }">
          <template v-if="temp !== null">
            <span class="temp-value">{{ temp.toFixed(1) }}</span>
            <span class="temp-unit">°C</span>
          </template>
          <template v-else>
            <span class="temp-value temp-na">--</span>
            <span class="temp-unit">°C</span>
          </template>
        </div>
        <div class="q-mt-sm">
          <q-badge :color="online ? 'green' : 'red'" :label="online ? 'В сети' : 'Не в сети'" />
        </div>
      </q-card-section>
    </q-card>

    <q-card dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Порог уведомления</div>
        <div class="threshold-row">
          <q-slider v-model="thresholdInput" :min="-10" :max="30" :step="0.5" color="primary" label :label-value="thresholdInput + '°C'" class="q-mr-md" style="flex:1;" @change="onThresholdChange" />
          <q-input v-model.number="thresholdInput" type="number" filled dense dark style="width:80px;" :step="0.5" @change="onThresholdChange(thresholdInput)" />
        </div>
      </q-card-section>
    </q-card>

    <q-card dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="notify-row">
          <span class="notify-label">Уведомления в Telegram</span>
          <q-toggle :model-value="notify" color="primary" @update:model-value="onNotifyToggle" />
        </div>
      </q-card-section>
    </q-card>

    <q-card dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="row items-center justify-between q-mb-xs">
          <div class="section-title" style="margin-bottom:0;">История температуры</div>
          <q-btn flat round dense icon="fullscreen" size="sm" color="grey-5" @click="enterChartFullscreen" />
        </div>

        <q-btn-toggle
          v-model="selectedHours" :options="hoursOptions"
          color="grey-8" text-color="grey-4" toggle-color="primary" toggle-text-color="dark"
          no-caps spread class="q-mb-sm" dense
        />

        <div class="chart-container" :class="{ 'chart-loading': chartLoading }">
          <q-inner-loading :showing="chartLoading" color="primary" />
          <Line v-if="chartData.labels.length > 0" :data="chartData" :options="chartOptions" />
          <div v-else-if="!chartLoading" class="text-center text-grey-6 q-pa-lg">Нет данных</div>
        </div>
      </q-card-section>
    </q-card>

    <!-- Chart fullscreen — teleported to body, outside Quasar layout -->
    <Teleport to="body">
      <div v-if="chartFullscreen" ref="chartWrapRef" class="temp-chart-fs-overlay">
        <div class="temp-chart-fs-inner">
          <Line
            v-if="chartData.labels.length > 0"
            :data="chartData"
            :options="chartOptions"
            :key="'fs'"
          />
          <div v-else class="text-center text-grey-6" style="margin:auto;">Нет данных</div>
        </div>
        <button class="temp-chart-fs-close" @click="exitChartFullscreen">
          <span class="material-icons">close</span>
        </button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.section-card { background: #1a1a2e !important; border-radius: 8px; }
.section-title {
  font-size: 0.85rem; font-weight: 600; color: #aaa;
  text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;
}

.temp-display { display: inline-flex; align-items: baseline; }
.temp-display.offline { opacity: 0.4; }
.temp-value { font-size: 4rem; font-weight: 700; color: #2ee8b7; line-height: 1; }
.temp-value.temp-na { color: #666; }
.temp-unit { font-size: 2rem; font-weight: 400; color: #888; margin-left: 4px; }

.threshold-row { display: flex; align-items: center; }
.notify-row { display: flex; align-items: center; justify-content: space-between; }
.notify-label { font-size: 0.9rem; color: #ccc; }

.chart-container { position: relative; height: 200px; min-height: 180px; }
.chart-loading { opacity: 0.5; }

@media (min-width: 400px) {
  .chart-container { height: 250px; }
}

@media (min-width: 768px) {
  .chart-container { height: 300px; }
}

@media (max-width: 359px) {
  .temp-value { font-size: 3rem; }
  .temp-unit { font-size: 1.5rem; }
}

@media (min-width: 768px) {
  .threshold-row { gap: 16px; }
}
</style>

<!-- Unscoped — Teleport renders outside component scope -->
<style>
.temp-chart-fs-overlay {
  position: fixed; inset: 0; z-index: 99999;
  background: #111;
  display: flex; align-items: stretch; justify-content: stretch;
  padding: 12px;
}
.temp-chart-fs-inner {
  flex: 1; position: relative; min-width: 0; min-height: 0;
}
.temp-chart-fs-close {
  position: fixed; top: 16px; right: 16px; z-index: 100000;
  width: 44px; height: 44px; border-radius: 50%; border: none; cursor: pointer;
  background: rgba(255,255,255,0.15); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 0;
}
.temp-chart-fs-close .material-icons { font-size: 28px; }
</style>
