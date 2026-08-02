<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  device: {
    type: Object,
    required: true
  },
  calibrationResult: {
    type: Object,
    default: null
  }
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

const freqFilterNames = ['Все', 'Высокие', 'Средние', 'Низкие']
const lightSubModes = ['Статика', 'Перелив', 'Радуга']

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

function calibrate() {
  emit('calibrate')
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
  calibrate()
}
</script>

<template>
  <div class="device-panel" :class="{ disabled }">
    <!-- Offline overlay -->
    <div v-if="disabled" class="offline-overlay">
      <div class="offline-msg">Устройство не в сети</div>
    </div>

    <!-- Power -->
    <section class="section">
      <button
        class="power-btn"
        :class="{ on: power, off: !power }"
        :disabled="disabled"
        @click="togglePower"
      >
        {{ power ? 'ON' : 'OFF' }}
      </button>
    </section>

    <!-- Modes -->
    <section class="section">
      <h3 class="section-title">Режимы</h3>
      <div class="mode-grid">
        <button
          v-for="(name, i) in modeNames"
          :key="i"
          class="mode-btn"
          :class="{ active: mode === i }"
          :disabled="disabled"
          @click="setMode(i)"
        >
          {{ name }}
        </button>
      </div>
    </section>

    <!-- Relays -->
    <section class="section">
      <h3 class="section-title">Реле</h3>
      <div class="relay-list">
        <div v-for="i in 4" :key="i" class="relay-row">
          <span class="relay-label">Реле {{ i }}</span>
          <button
            class="relay-btn"
            :class="{ on: state['r' + i] }"
            :disabled="disabled"
            @click="setRelay(i - 1, !state['r' + i])"
          >
            {{ state['r' + i] ? 'ON' : 'OFF' }}
          </button>
        </div>
      </div>
    </section>

    <!-- General sliders -->
    <section class="section">
      <h3 class="section-title">Общие</h3>
      <div class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Яркость</span>
          <input
            type="range"
            min="0"
            max="255"
            :value="state.br ?? 128"
            :disabled="disabled"
            @input="onSliderDirect('br', $event.target.value)"
          />
          <span class="slider-value">{{ state.br ?? 128 }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Фон яркость</span>
          <input
            type="range"
            min="0"
            max="255"
            :value="state.ebr ?? 0"
            :disabled="disabled"
            @input="onSliderDirect('ebr', $event.target.value)"
          />
          <span class="slider-value">{{ state.ebr ?? 0 }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Фон цвет</span>
          <input
            type="range"
            min="0"
            max="255"
            :value="state.ecol ?? 0"
            :disabled="disabled"
            @input="onSliderDirect('ecol', $event.target.value)"
          />
          <span class="slider-value">{{ state.ecol ?? 0 }}</span>
        </label>
      </div>
    </section>

    <!-- VU meter -->
    <section v-if="showVU" class="section">
      <h3 class="section-title">VU-метр</h3>
      <div class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Плавность</span>
          <input
            type="range"
            min="5"
            max="100"
            :value="Math.round((state.sm ?? 0.5) * 100)"
            :disabled="disabled"
            @input="onSlider('sm', Number($event.target.value), 100)"
          />
          <span class="slider-value">{{ (state.sm ?? 0.5).toFixed(2) }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Усиление</span>
          <input
            type="range"
            min="10"
            max="30"
            :value="Math.round((state.exp ?? 1.5) * 10)"
            :disabled="disabled"
            @input="onSlider('exp', Number($event.target.value), 10)"
          />
          <span class="slider-value">{{ (state.exp ?? 1.5).toFixed(1) }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Авто-макс</span>
          <input
            type="range"
            min="10"
            max="30"
            :value="Math.round((state.mc ?? 1.5) * 10)"
            :disabled="disabled"
            @input="onSlider('mc', Number($event.target.value), 10)"
          />
          <span class="slider-value">{{ (state.mc ?? 1.5).toFixed(1) }}</span>
        </label>
      </div>
    </section>

    <!-- Rainbow -->
    <section v-if="showRainbow" class="section">
      <h3 class="section-title">Радуга</h3>
      <div class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Шаг радуги</span>
          <input
            type="range"
            min="1"
            max="40"
            :value="Math.round((state.rs ?? 5) * 2)"
            :disabled="disabled"
            @input="onSlider('rs', Number($event.target.value), 2)"
          />
          <span class="slider-value">{{ (state.rs ?? 5).toFixed(1) }}</span>
        </label>
      </div>
    </section>

    <!-- Color Music -->
    <section v-if="showColorMusic" class="section">
      <h3 class="section-title">Цветомузыка</h3>
      <div class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Плавность</span>
          <input
            type="range"
            min="5"
            max="100"
            :value="Math.round((state.smf ?? 0.5) * 100)"
            :disabled="disabled"
            @input="onSlider('smf', Number($event.target.value), 100)"
          />
          <span class="slider-value">{{ (state.smf ?? 0.5).toFixed(2) }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Чувствит.</span>
          <input
            type="range"
            min="0"
            max="50"
            :value="Math.round((state.mcf ?? 2.0) * 10)"
            :disabled="disabled"
            @input="onSlider('mcf', Number($event.target.value), 10)"
          />
          <span class="slider-value">{{ (state.mcf ?? 2.0).toFixed(1) }}</span>
        </label>
      </div>
    </section>

    <!-- Freq Filter -->
    <section v-if="showFreqFilter" class="section">
      <h3 class="section-title">Частотный фильтр</h3>
      <div class="btn-row">
        <button
          v-for="(name, i) in freqFilterNames"
          :key="i"
          class="choice-btn"
          :class="{ active: (state.fsm ?? 0) === i }"
          :disabled="disabled"
          @click="setFreqFilter(i)"
        >
          {{ name }}
        </button>
      </div>
    </section>

    <!-- Strobe -->
    <section v-if="showStrobe" class="section">
      <h3 class="section-title">Стробоскоп</h3>
      <div class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Период мс</span>
          <input
            type="range"
            min="10"
            max="1000"
            step="10"
            :value="state.sp ?? 100"
            :disabled="disabled"
            @input="onSliderDirect('sp', $event.target.value)"
          />
          <span class="slider-value">{{ state.sp ?? 100 }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Плавность</span>
          <input
            type="range"
            min="1"
            max="255"
            :value="state.ss ?? 128"
            :disabled="disabled"
            @input="onSliderDirect('ss', $event.target.value)"
          />
          <span class="slider-value">{{ state.ss ?? 128 }}</span>
        </label>
      </div>
    </section>

    <!-- Light (Backlight) -->
    <section v-if="showLight" class="section">
      <h3 class="section-title">Подсветка</h3>
      <div class="btn-row mb">
        <button
          v-for="(name, i) in lightSubModes"
          :key="i"
          class="choice-btn"
          :class="{ active: lightMode === i }"
          :disabled="disabled"
          @click="setLightMode(i)"
        >
          {{ name }}
        </button>
      </div>

      <!-- Static -->
      <div v-if="lightMode === 0" class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Цвет</span>
          <input
            type="range"
            min="0"
            max="255"
            :value="state.lc ?? 0"
            :disabled="disabled"
            @input="onSliderDirect('lc', $event.target.value)"
          />
          <span class="slider-value">{{ state.lc ?? 0 }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Насыщенн.</span>
          <input
            type="range"
            min="0"
            max="255"
            :value="state.ls ?? 255"
            :disabled="disabled"
            @input="onSliderDirect('ls', $event.target.value)"
          />
          <span class="slider-value">{{ state.ls ?? 255 }}</span>
        </label>
      </div>

      <!-- Overflow (Перелив) -->
      <div v-if="lightMode === 1" class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Скорость</span>
          <input
            type="range"
            min="1"
            max="255"
            :value="state.cs ?? 128"
            :disabled="disabled"
            @input="onSliderDirect('cs', $event.target.value)"
          />
          <span class="slider-value">{{ state.cs ?? 128 }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Насыщенн.</span>
          <input
            type="range"
            min="0"
            max="255"
            :value="state.ls ?? 255"
            :disabled="disabled"
            @input="onSliderDirect('ls', $event.target.value)"
          />
          <span class="slider-value">{{ state.ls ?? 255 }}</span>
        </label>
      </div>

      <!-- Rainbow sub-mode -->
      <div v-if="lightMode === 2" class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Шаг</span>
          <input
            type="range"
            min="1"
            max="20"
            :value="Math.round((state.rs2 ?? 3) * 2)"
            :disabled="disabled"
            @input="onSlider('rs2', Number($event.target.value), 2)"
          />
          <span class="slider-value">{{ (state.rs2 ?? 3).toFixed(1) }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Период</span>
          <input
            type="range"
            min="-20"
            max="20"
            :value="state.rp ?? 0"
            :disabled="disabled"
            @input="onSliderDirect('rp', $event.target.value)"
          />
          <span class="slider-value">{{ state.rp ?? 0 }}</span>
        </label>
      </div>
    </section>

    <!-- Running lights -->
    <section v-if="showRunning" class="section">
      <h3 class="section-title">Бегущие огни</h3>
      <div class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Скорость</span>
          <input
            type="range"
            min="1"
            max="255"
            :value="state.rns ?? 128"
            :disabled="disabled"
            @input="onSliderDirect('rns', $event.target.value)"
          />
          <span class="slider-value">{{ state.rns ?? 128 }}</span>
        </label>
      </div>
    </section>

    <!-- Analyzer -->
    <section v-if="showAnalyzer" class="section">
      <h3 class="section-title">Анализатор</h3>
      <div class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Начало цвет</span>
          <input
            type="range"
            min="0"
            max="255"
            :value="state.hs ?? 0"
            :disabled="disabled"
            @input="onSliderDirect('hs', $event.target.value)"
          />
          <span class="slider-value">{{ state.hs ?? 0 }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Шаг цвета</span>
          <input
            type="range"
            min="1"
            max="255"
            :value="state.hst ?? 10"
            :disabled="disabled"
            @input="onSliderDirect('hst', $event.target.value)"
          />
          <span class="slider-value">{{ state.hst ?? 10 }}</span>
        </label>
      </div>
    </section>

    <!-- Audio -->
    <section class="section">
      <h3 class="section-title">Аудио</h3>
      <div class="slider-group">
        <label class="slider-row">
          <span class="slider-label">Порог VU</span>
          <input
            type="range"
            min="0"
            max="800"
            :value="state.lp ?? 0"
            :disabled="disabled"
            @input="onSliderDirect('lp', $event.target.value)"
          />
          <span class="slider-value">{{ state.lp ?? 0 }}</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Порог спектра</span>
          <input
            type="range"
            min="0"
            max="500"
            :value="state.slp ?? 0"
            :disabled="disabled"
            @input="onSliderDirect('slp', $event.target.value)"
          />
          <span class="slider-value">{{ state.slp ?? 0 }}</span>
        </label>
      </div>
      <div class="cal-row">
        <button
          class="cal-btn"
          :disabled="disabled || calibrating"
          @click="doCalibrate"
        >
          {{ calibrating ? 'Калибровка...' : 'Калибровка' }}
        </button>
        <div v-if="calValues" class="cal-result">
          <span class="cal-label">Результат:</span>
          <span class="cal-values">{{ JSON.stringify(calValues) }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.device-panel {
  position: relative;
}

.disabled {
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

.offline-msg {
  color: #f44336;
  font-size: 1.1rem;
  font-weight: 600;
  padding: 16px 28px;
  background: #1a1a2e;
  border-radius: 8px;
  border: 1px solid #f44336;
}

.section {
  background: #1a1a2e;
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 10px;
}

.section-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: #aaa;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 10px;
}

/* Power button */
.power-btn {
  width: 100%;
  padding: 14px;
  border-radius: 8px;
  font-size: 1.3rem;
  font-weight: 700;
  letter-spacing: 2px;
  transition: all 0.2s;
}

.power-btn.on {
  background: #4caf50;
  color: #fff;
  box-shadow: 0 0 20px rgba(76, 175, 80, 0.4);
}

.power-btn.off {
  background: #c62828;
  color: #fff;
  box-shadow: 0 0 20px rgba(198, 40, 40, 0.3);
}

/* Mode grid */
.mode-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}

.mode-btn {
  padding: 8px 4px;
  border-radius: 6px;
  background: #252545;
  color: #ccc;
  font-size: 0.75rem;
  transition: all 0.15s;
  border: 2px solid transparent;
}

.mode-btn.active {
  border-color: #2ee8b7;
  color: #2ee8b7;
  background: rgba(46, 232, 183, 0.1);
}

.mode-btn:not(.active):hover {
  background: #2d2d50;
}

/* Relays */
.relay-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.relay-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
}

.relay-label {
  font-size: 0.9rem;
  color: #ccc;
}

.relay-btn {
  padding: 6px 20px;
  border-radius: 6px;
  font-size: 0.8rem;
  font-weight: 600;
  background: #333;
  color: #aaa;
  transition: all 0.15s;
  min-width: 60px;
}

.relay-btn.on {
  background: #4caf50;
  color: #fff;
}

/* Sliders */
.slider-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
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

/* Button row */
.btn-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.btn-row.mb {
  margin-bottom: 12px;
}

.choice-btn {
  flex: 1;
  min-width: 0;
  padding: 8px 6px;
  border-radius: 6px;
  background: #252545;
  color: #ccc;
  font-size: 0.8rem;
  transition: all 0.15s;
  border: 2px solid transparent;
}

.choice-btn.active {
  border-color: #2ee8b7;
  color: #2ee8b7;
  background: rgba(46, 232, 183, 0.1);
}

/* Calibration */
.cal-row {
  margin-top: 12px;
}

.cal-btn {
  padding: 10px 24px;
  border-radius: 6px;
  background: #2ee8b7;
  color: #111;
  font-weight: 600;
  font-size: 0.85rem;
  transition: opacity 0.2s;
}

.cal-btn:hover {
  opacity: 0.85;
}

.cal-result {
  margin-top: 8px;
  font-size: 0.8rem;
  color: #aaa;
  word-break: break-all;
}

.cal-label {
  color: #2ee8b7;
  margin-right: 6px;
}

.cal-values {
  font-family: monospace;
}
</style>
