<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  device: { type: Object, required: true },
  calibrationResult: { type: Object, default: null }
})

const emit = defineEmits(['command', 'calibrate'])

const disabled = computed(() => !props.device.online)
const state = computed(() => props.device.state || {})
const mode = computed(() => state.value.mode ?? 0)
const power = computed(() => !!(state.value.on))

const modeNames = [
  'VU Градиент',
  'VU Радуга',
  'Цвет 5 полос',
  'Цвет 3 полосы',
  'Цвет Вспышки',
  'Стробоскоп',
  'Подсветка',
  'Бегущие огни',
  'Анализатор'
]

const modeOptions = modeNames.map((label, i) => ({ label, value: i }))

const freqFilterOptions = [
  { label: 'Все', value: 0 },
  { label: 'Высокие', value: 1 },
  { label: 'Средние', value: 2 },
  { label: 'Низкие', value: 3 }
]

const lightSubModeOptions = [
  { label: 'Статика', value: 0 },
  { label: 'Перелив', value: 1 },
  { label: 'Радуга', value: 2 }
]

const showVU = computed(() => [0, 1].includes(mode.value))
const showRainbow = computed(() => mode.value === 1)
const showColorMusic = computed(() => [2, 3, 4, 7, 8].includes(mode.value))
const showFreqFilter = computed(() => [4, 7].includes(mode.value))
const showStrobe = computed(() => mode.value === 5)
const showLight = computed(() => mode.value === 6)
const showRunning = computed(() => mode.value === 7)
const showAnalyzer = computed(() => mode.value === 8)

const lightMode = computed(() => state.value.lm ?? 0)

// Debounce timers
const debounceTimers = {}

function send(params) {
  emit('command', params)
}

function sendDebounced(key, params) {
  if (debounceTimers[key]) {
    clearTimeout(debounceTimers[key])
  }
  debounceTimers[key] = setTimeout(() => {
    send(params)
    delete debounceTimers[key]
  }, 100)
}

function togglePower() {
  send({ on: power.value ? 0 : 1 })
}

function setMode(m) {
  send({ mode: m })
}

function setRelay(index, value) {
  const key = 'r' + (index + 1)
  send({ [key]: value ? 1 : 0 })
}

function onSlider(param, rawValue, divisor) {
  const val = divisor ? rawValue / divisor : Number(rawValue)
  sendDebounced(param, { [param]: val })
}

function onSliderDirect(param, rawValue) {
  sendDebounced(param, { [param]: Number(rawValue) })
}

function setFreqFilter(val) {
  send({ fsm: val })
}

function setLightMode(val) {
  send({ lm: val })
}

const calibrating = ref(false)
const calValues = ref(null)

watch(() => props.calibrationResult, (res) => {
  if (res && res.deviceId === props.device.id) {
    calibrating.value = false
    calValues.value = res.values
  }
})

function doCalibrate() {
  calibrating.value = true
  calValues.value = null
  emit('calibrate')
}
</script>

<template>
  <div class="color-music-panel" :class="{ 'panel-disabled': disabled }">
    <!-- Offline overlay -->
    <div v-if="disabled" class="offline-overlay">
      <q-banner class="bg-negative text-white" rounded>
        Устройство не в сети
      </q-banner>
    </div>

    <!-- Power -->
    <q-card dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <q-btn
          :label="power ? 'ON' : 'OFF'"
          :color="power ? 'green' : 'red-8'"
          class="full-width power-btn"
          :disable="disabled"
          size="lg"
          no-caps
          @click="togglePower"
        />
      </q-card-section>
    </q-card>

    <!-- Modes -->
    <q-card dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Режимы</div>
        <div class="mode-grid">
          <q-btn
            v-for="(name, i) in modeNames"
            :key="i"
            :label="name"
            :outline="mode !== i"
            :color="mode === i ? 'primary' : undefined"
            :text-color="mode === i ? 'dark' : 'grey-5'"
            class="mode-btn"
            :class="{ 'mode-active': mode === i }"
            :disable="disabled"
            dense
            no-caps
            @click="setMode(i)"
          />
        </div>
      </q-card-section>
    </q-card>

    <!-- Relays -->
    <q-card dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Реле</div>
        <div class="relay-list">
          <div v-for="i in 4" :key="i" class="relay-row">
            <span class="relay-label">Реле {{ i }}</span>
            <q-toggle
              :model-value="!!state['r' + i]"
              color="green"
              :disable="disabled"
              @update:model-value="setRelay(i - 1, $event)"
            />
          </div>
        </div>
      </q-card-section>
    </q-card>

    <!-- General sliders -->
    <q-card dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Общие</div>
        <div class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Яркость</span>
            <q-slider
              :model-value="state.br ?? 128"
              :min="0" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('br', $event)"
            />
            <span class="slider-value">{{ state.br ?? 128 }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Фон яркость</span>
            <q-slider
              :model-value="state.ebr ?? 0"
              :min="0" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('ebr', $event)"
            />
            <span class="slider-value">{{ state.ebr ?? 0 }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Фон цвет</span>
            <q-slider
              :model-value="state.ecol ?? 0"
              :min="0" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('ecol', $event)"
            />
            <span class="slider-value">{{ state.ecol ?? 0 }}</span>
          </div>
        </div>
      </q-card-section>
    </q-card>

    <!-- VU meter -->
    <q-card v-if="showVU" dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">VU-метр</div>
        <div class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Плавность</span>
            <q-slider
              :model-value="Math.round((state.sm ?? 0.5) * 100)"
              :min="5" :max="100"
              color="primary"
              :disable="disabled"
              @update:model-value="onSlider('sm', $event, 100)"
            />
            <span class="slider-value">{{ (state.sm ?? 0.5).toFixed(2) }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Усиление</span>
            <q-slider
              :model-value="Math.round((state.exp ?? 1.5) * 10)"
              :min="10" :max="30"
              color="primary"
              :disable="disabled"
              @update:model-value="onSlider('exp', $event, 10)"
            />
            <span class="slider-value">{{ (state.exp ?? 1.5).toFixed(1) }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Авто-макс</span>
            <q-slider
              :model-value="Math.round((state.mc ?? 1.5) * 10)"
              :min="10" :max="30"
              color="primary"
              :disable="disabled"
              @update:model-value="onSlider('mc', $event, 10)"
            />
            <span class="slider-value">{{ (state.mc ?? 1.5).toFixed(1) }}</span>
          </div>
        </div>
      </q-card-section>
    </q-card>

    <!-- Rainbow -->
    <q-card v-if="showRainbow" dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Радуга</div>
        <div class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Шаг радуги</span>
            <q-slider
              :model-value="Math.round((state.rs ?? 5) * 2)"
              :min="1" :max="40"
              color="primary"
              :disable="disabled"
              @update:model-value="onSlider('rs', $event, 2)"
            />
            <span class="slider-value">{{ (state.rs ?? 5).toFixed(1) }}</span>
          </div>
        </div>
      </q-card-section>
    </q-card>

    <!-- Color Music -->
    <q-card v-if="showColorMusic" dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Цветомузыка</div>
        <div class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Плавность</span>
            <q-slider
              :model-value="Math.round((state.smf ?? 0.5) * 100)"
              :min="5" :max="100"
              color="primary"
              :disable="disabled"
              @update:model-value="onSlider('smf', $event, 100)"
            />
            <span class="slider-value">{{ (state.smf ?? 0.5).toFixed(2) }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Чувствит.</span>
            <q-slider
              :model-value="Math.round((state.mcf ?? 2.0) * 10)"
              :min="0" :max="50"
              color="primary"
              :disable="disabled"
              @update:model-value="onSlider('mcf', $event, 10)"
            />
            <span class="slider-value">{{ (state.mcf ?? 2.0).toFixed(1) }}</span>
          </div>
        </div>
      </q-card-section>
    </q-card>

    <!-- Freq Filter -->
    <q-card v-if="showFreqFilter" dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Частотный фильтр</div>
        <q-btn-toggle
          :model-value="state.fsm ?? 0"
          :options="freqFilterOptions"
          color="grey-8"
          text-color="grey-4"
          toggle-color="primary"
          toggle-text-color="dark"
          no-caps
          spread
          :disable="disabled"
          @update:model-value="setFreqFilter"
        />
      </q-card-section>
    </q-card>

    <!-- Strobe -->
    <q-card v-if="showStrobe" dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Стробоскоп</div>
        <div class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Период мс</span>
            <q-slider
              :model-value="state.sp ?? 100"
              :min="10" :max="1000" :step="10"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('sp', $event)"
            />
            <span class="slider-value">{{ state.sp ?? 100 }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Плавность</span>
            <q-slider
              :model-value="state.ss ?? 128"
              :min="1" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('ss', $event)"
            />
            <span class="slider-value">{{ state.ss ?? 128 }}</span>
          </div>
        </div>
      </q-card-section>
    </q-card>

    <!-- Light (Backlight) -->
    <q-card v-if="showLight" dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Подсветка</div>
        <q-btn-toggle
          :model-value="lightMode"
          :options="lightSubModeOptions"
          color="grey-8"
          text-color="grey-4"
          toggle-color="primary"
          toggle-text-color="dark"
          no-caps
          spread
          class="q-mb-md"
          :disable="disabled"
          @update:model-value="setLightMode"
        />

        <!-- Static -->
        <div v-if="lightMode === 0" class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Цвет</span>
            <q-slider
              :model-value="state.lc ?? 0"
              :min="0" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('lc', $event)"
            />
            <span class="slider-value">{{ state.lc ?? 0 }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Насыщенн.</span>
            <q-slider
              :model-value="state.ls ?? 255"
              :min="0" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('ls', $event)"
            />
            <span class="slider-value">{{ state.ls ?? 255 }}</span>
          </div>
        </div>

        <!-- Overflow (Перелив) -->
        <div v-if="lightMode === 1" class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Скорость</span>
            <q-slider
              :model-value="state.cs ?? 128"
              :min="1" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('cs', $event)"
            />
            <span class="slider-value">{{ state.cs ?? 128 }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Насыщенн.</span>
            <q-slider
              :model-value="state.ls ?? 255"
              :min="0" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('ls', $event)"
            />
            <span class="slider-value">{{ state.ls ?? 255 }}</span>
          </div>
        </div>

        <!-- Rainbow sub-mode -->
        <div v-if="lightMode === 2" class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Шаг</span>
            <q-slider
              :model-value="Math.round((state.rs2 ?? 3) * 2)"
              :min="1" :max="20"
              color="primary"
              :disable="disabled"
              @update:model-value="onSlider('rs2', $event, 2)"
            />
            <span class="slider-value">{{ (state.rs2 ?? 3).toFixed(1) }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Период</span>
            <q-slider
              :model-value="state.rp ?? 0"
              :min="-20" :max="20"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('rp', $event)"
            />
            <span class="slider-value">{{ state.rp ?? 0 }}</span>
          </div>
        </div>
      </q-card-section>
    </q-card>

    <!-- Running lights -->
    <q-card v-if="showRunning" dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Бегущие огни</div>
        <div class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Скорость</span>
            <q-slider
              :model-value="state.rns ?? 128"
              :min="1" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('rns', $event)"
            />
            <span class="slider-value">{{ state.rns ?? 128 }}</span>
          </div>
        </div>
      </q-card-section>
    </q-card>

    <!-- Analyzer -->
    <q-card v-if="showAnalyzer" dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Анализатор</div>
        <div class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Начало цвет</span>
            <q-slider
              :model-value="state.hs ?? 0"
              :min="0" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('hs', $event)"
            />
            <span class="slider-value">{{ state.hs ?? 0 }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Шаг цвета</span>
            <q-slider
              :model-value="state.hst ?? 10"
              :min="1" :max="255"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('hst', $event)"
            />
            <span class="slider-value">{{ state.hst ?? 10 }}</span>
          </div>
        </div>
      </q-card-section>
    </q-card>

    <!-- Audio -->
    <q-card dark class="section-card q-mb-sm">
      <q-card-section class="q-pa-sm">
        <div class="section-title">Аудио</div>
        <div class="slider-group">
          <div class="slider-row">
            <span class="slider-label">Порог VU</span>
            <q-slider
              :model-value="state.lp ?? 0"
              :min="0" :max="800"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('lp', $event)"
            />
            <span class="slider-value">{{ state.lp ?? 0 }}</span>
          </div>
          <div class="slider-row">
            <span class="slider-label">Порог спектра</span>
            <q-slider
              :model-value="state.slp ?? 0"
              :min="0" :max="500"
              color="primary"
              :disable="disabled"
              @update:model-value="onSliderDirect('slp', $event)"
            />
            <span class="slider-value">{{ state.slp ?? 0 }}</span>
          </div>
        </div>

        <div class="q-mt-md">
          <q-btn
            :label="calibrating ? 'Калибровка...' : 'Калибровка'"
            color="primary"
            text-color="dark"
            :loading="calibrating"
            :disable="disabled || calibrating"
            no-caps
            @click="doCalibrate"
          />
          <div v-if="calValues" class="q-mt-sm text-grey-5" style="font-size: 0.8rem;">
            <span style="color: #2ee8b7;">Результат:</span>
            <span class="cal-values">{{ JSON.stringify(calValues) }}</span>
          </div>
        </div>
      </q-card-section>
    </q-card>
  </div>
</template>

<style scoped>
.color-music-panel {
  position: relative;
}

.panel-disabled {
  pointer-events: none;
}

.offline-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  pointer-events: all;
}

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

.power-btn {
  font-weight: 700;
  letter-spacing: 2px;
}

.mode-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}

.mode-btn {
  font-size: 0.75rem !important;
  border: 2px solid transparent !important;
}

.mode-active {
  border-color: #2ee8b7 !important;
  background: rgba(46, 232, 183, 0.1) !important;
}

.relay-list {
  display: flex;
  flex-direction: column;
}

.relay-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 0;
}

.relay-label {
  font-size: 0.9rem;
  color: #ccc;
}

.slider-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.slider-row {
  display: grid;
  grid-template-columns: 100px 1fr 50px;
  align-items: center;
  gap: 10px;
}

.slider-label {
  font-size: 0.82rem;
  color: #aaa;
  white-space: nowrap;
}

.slider-value {
  font-size: 0.8rem;
  color: #2ee8b7;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.cal-values {
  font-family: monospace;
  word-break: break-all;
}
</style>
