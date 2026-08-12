<script setup>
import { ref, computed, defineAsyncComponent } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth.js'
import { useWebSocket } from '../composables/useWebSocket.js'

const ColorMusicPanel = defineAsyncComponent(() => import('../components/ColorMusicPanel.vue'))
const TempSensorPanel = defineAsyncComponent(() => import('../components/TempSensorPanel.vue'))
const CameraPanel = defineAsyncComponent(() => import('../components/CameraPanel.vue'))

const router = useRouter()
const { logout } = useAuth()
const { devices, connected, calibrationResult, sendCommand, requestCalibrate } = useWebSocket()

const activeTab = ref('cameras')

const navTabs = [
  { id: 'home', icon: 'home', label: 'Дом' },
  { id: 'cameras', icon: 'videocam', label: 'Камеры' },
  { id: 'tempsensor', icon: 'device_thermostat', label: 'Термометр' },
  { id: 'colormusic', icon: 'music_note', label: 'Свет-Музыка' }
]

const tempDevices = computed(() => devices.value.filter(d => d.deviceType === 'tempsensor'))
const cmDevices = computed(() => devices.value.filter(d => d.deviceType === 'colormusic'))
const activeTempDevice = computed(() => tempDevices.value[0])
const activeCmDevice = computed(() => cmDevices.value[0])

const pageTitle = computed(() => {
  if (activeTab.value === 'home') return 'Smart Home'
  const t = navTabs.find(n => n.id === activeTab.value)
  return t?.label ?? 'Smart Home'
})

function doLogout() {
  logout()
  router.push('/login')
}
</script>

<template>
  <div class="shell">
    <!-- Desktop sidebar -->
    <aside class="sidebar">
      <div class="sidebar-logo">SH</div>
      <nav class="sidebar-nav">
        <button
          v-for="t in navTabs" :key="t.id"
          class="sidebar-btn" :class="{ active: activeTab === t.id }"
          @click="activeTab = t.id"
        >
          <span class="material-icons">{{ t.icon }}</span>
          <span class="sidebar-lbl">{{ t.label }}</span>
        </button>
      </nav>
      <div class="sidebar-foot">
        <span class="conn-dot" :class="connected ? 'on' : 'off'"></span>
        <button class="sidebar-btn" @click="doLogout">
          <span class="material-icons">logout</span>
          <span class="sidebar-lbl">Выход</span>
        </button>
      </div>
    </aside>

    <!-- Main area -->
    <div class="main">
      <!-- Mobile header -->
      <header class="hdr-m">
        <h1 class="hdr-title">{{ pageTitle }}</h1>
        <div class="hdr-r">
          <span class="conn-dot" :class="connected ? 'on' : 'off'"></span>
          <button class="icon-btn" @click="doLogout" title="Выйти">
            <span class="material-icons">logout</span>
          </button>
        </div>
      </header>

      <!-- Desktop header -->
      <header class="hdr-d">
        <h1 class="hdr-title">{{ pageTitle }}</h1>
        <div class="hdr-r">
          <span class="conn-dot" :class="connected ? 'on' : 'off'"></span>
          <span class="conn-label">{{ connected ? 'Подключено' : 'Нет связи' }}</span>
        </div>
      </header>

      <!-- Content -->
      <main class="content" :class="'tab-' + activeTab">
        <!-- Home -->
        <div v-if="activeTab === 'home'" class="view-home">
          <div class="home-grid">
            <button class="hcard glass" @click="activeTab = 'cameras'">
              <span class="material-icons hcard-icon hcard-icon--cam">videocam</span>
              <span class="hcard-val">Камеры</span>
              <span class="hcard-sub">Видеонаблюдение</span>
            </button>
            <button class="hcard glass" @click="activeTab = 'tempsensor'">
              <span class="material-icons hcard-icon hcard-icon--temp">device_thermostat</span>
              <span class="hcard-val">{{ activeTempDevice?.state?.temp?.toFixed(1) ?? '--' }}°C</span>
              <span class="hcard-sub">Термометр</span>
            </button>
            <button class="hcard glass" @click="activeTab = 'colormusic'">
              <span class="material-icons hcard-icon hcard-icon--cm">music_note</span>
              <span class="hcard-val">{{ activeCmDevice?.state?.on ? 'Активна' : 'Выкл' }}</span>
              <span class="hcard-sub">Свет-Музыка</span>
            </button>
            <div class="hcard glass">
              <span class="material-icons hcard-icon hcard-icon--net">{{ connected ? 'wifi' : 'wifi_off' }}</span>
              <span class="hcard-val">{{ connected ? 'Онлайн' : 'Оффлайн' }}</span>
              <span class="hcard-sub">Сервер</span>
            </div>
          </div>
        </div>

        <!-- Cameras -->
        <CameraPanel v-if="activeTab === 'cameras'" />

        <!-- Temperature -->
        <template v-if="activeTab === 'tempsensor'">
          <TempSensorPanel
            v-if="activeTempDevice"
            :device="activeTempDevice"
            @command="(p) => sendCommand(activeTempDevice.id, p)"
          />
          <div v-else class="empty-state">
            <span class="material-icons empty-icon">device_thermostat</span>
            <p>Датчик не подключен</p>
          </div>
        </template>

        <!-- Color Music -->
        <template v-if="activeTab === 'colormusic'">
          <ColorMusicPanel
            v-if="activeCmDevice"
            :device="activeCmDevice"
            :calibration-result="calibrationResult"
            @command="(p) => sendCommand(activeCmDevice.id, p)"
            @calibrate="requestCalibrate(activeCmDevice.id)"
          />
          <div v-else class="empty-state">
            <span class="material-icons empty-icon">music_note</span>
            <p>Устройство не подключено</p>
          </div>
        </template>
      </main>
    </div>

    <!-- Bottom nav (mobile) -->
    <nav class="bnav">
      <button
        v-for="t in navTabs" :key="t.id"
        class="bnav-btn" :class="{ active: activeTab === t.id }"
        @click="activeTab = t.id"
      >
        <span class="material-icons bnav-icon">{{ t.icon }}</span>
        <span class="bnav-lbl">{{ t.label }}</span>
      </button>
    </nav>
  </div>
</template>

<style scoped>
/* ===== Shell ===== */
.shell {
  min-height: 100dvh;
  background:
    radial-gradient(ellipse at 20% 80%, rgba(46, 232, 183, 0.03) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 20%, rgba(100, 110, 220, 0.02) 0%, transparent 50%),
    #070b14;
  color: #e8e8e8;
  display: flex;
  flex-direction: column;
}

/* ===== Glass utility ===== */
.glass {
  background: rgba(255, 255, 255, 0.04);
  backdrop-filter: blur(20px) saturate(150%);
  -webkit-backdrop-filter: blur(20px) saturate(150%);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 16px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15),
              inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

/* ===== Sidebar (desktop only) ===== */
.sidebar { display: none; }

/* ===== Main area ===== */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* ===== Headers ===== */
.hdr-m {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: calc(12px + env(safe-area-inset-top, 0)) 16px 8px;
  flex-shrink: 0;
}
.hdr-d { display: none; }

.hdr-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: #2ee8b7;
  margin: 0;
  line-height: 1.2;
}

.hdr-r {
  display: flex;
  align-items: center;
  gap: 10px;
}

.icon-btn {
  width: 36px; height: 36px;
  border-radius: 50%;
  border: none;
  background: rgba(255, 255, 255, 0.06);
  color: #888;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s;
}
.icon-btn .material-icons { font-size: 20px; }
.icon-btn:active { background: rgba(255, 255, 255, 0.12); }

.conn-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.conn-dot.on { background: #4caf50; box-shadow: 0 0 6px #4caf50; }
.conn-dot.off { background: #f44336; box-shadow: 0 0 6px #f44336; }

.conn-label { font-size: 0.82rem; color: #888; }

/* ===== Content ===== */
.content {
  flex: 1;
  overflow-y: auto;
  padding: 0 12px 12px;
  padding-bottom: calc(74px + env(safe-area-inset-bottom, 0));
}

/* ===== Home ===== */
.home-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  padding-top: 8px;
}

.hcard {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 18px 16px;
  cursor: pointer;
  text-align: left;
  transition: background 0.2s;
}
.hcard:active { background: rgba(255, 255, 255, 0.08); }

.hcard-icon { font-size: 28px; margin-bottom: 12px; }
.hcard-icon--cam { color: #2ee8b7; }
.hcard-icon--temp { color: #4fc3f7; }
.hcard-icon--cm { color: #ce93d8; }
.hcard-icon--net { color: #81c784; }

.hcard-val {
  font-size: 1.15rem;
  font-weight: 600;
  color: #eee;
  margin-bottom: 4px;
}
.hcard-sub { font-size: 0.78rem; color: #666; }

/* ===== Empty state ===== */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 64px 16px;
  text-align: center;
}
.empty-icon { font-size: 48px; color: #333; margin-bottom: 12px; }
.empty-state p { color: #555; margin: 0; }

/* ===== Bottom nav (mobile) ===== */
.bnav {
  position: fixed;
  bottom: 0; left: 0; right: 0;
  display: flex;
  justify-content: space-around;
  align-items: flex-start;
  background: rgba(10, 14, 26, 0.82);
  backdrop-filter: blur(24px) saturate(150%);
  -webkit-backdrop-filter: blur(24px) saturate(150%);
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  padding: 6px 0 calc(6px + env(safe-area-inset-bottom, 0));
  z-index: 1000;
}

.bnav-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 6px 0;
  background: none;
  border: none;
  cursor: pointer;
  color: #555;
  transition: color 0.2s;
}
.bnav-btn.active { color: #2ee8b7; }

.bnav-icon { font-size: 24px; }
.bnav-lbl { font-size: 0.62rem; font-weight: 500; letter-spacing: 0.2px; }

/* ===== Desktop (>= 1024px) ===== */
@media (min-width: 1024px) {
  .shell { flex-direction: row; }

  /* Sidebar */
  .sidebar {
    display: flex;
    flex-direction: column;
    align-items: center;
    width: 82px;
    position: fixed;
    top: 0; left: 0; bottom: 0;
    background: rgba(10, 14, 26, 0.6);
    backdrop-filter: blur(24px) saturate(150%);
    -webkit-backdrop-filter: blur(24px) saturate(150%);
    border-right: 1px solid rgba(255, 255, 255, 0.06);
    padding: 20px 0 16px;
    z-index: 1000;
  }

  .sidebar-logo {
    font-size: 1.1rem;
    font-weight: 800;
    color: #2ee8b7;
    margin-bottom: 28px;
    letter-spacing: 1px;
  }

  .sidebar-nav {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    width: 100%;
    padding: 0 8px;
  }

  .sidebar-btn {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    padding: 10px 4px;
    background: none;
    border: none;
    border-radius: 12px;
    color: #555;
    cursor: pointer;
    transition: color 0.2s, background 0.2s;
  }
  .sidebar-btn:hover { background: rgba(255, 255, 255, 0.05); color: #aaa; }
  .sidebar-btn.active { color: #2ee8b7; background: rgba(46, 232, 183, 0.08); }
  .sidebar-btn .material-icons { font-size: 22px; }
  .sidebar-lbl { font-size: 0.58rem; font-weight: 500; white-space: nowrap; }

  .sidebar-foot {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 0 8px;
  }

  /* Main */
  .main { margin-left: 82px; }
  .hdr-m { display: none; }
  .hdr-d {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 20px 32px 12px;
    flex-shrink: 0;
  }

  .content {
    padding: 0 32px 32px;
    max-width: 960px;
  }
  .content.tab-cameras {
    max-width: 1100px;
  }

  .bnav { display: none; }

  .home-grid {
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
  }
  .hcard { padding: 24px 20px; }
  .hcard:hover { background: rgba(255, 255, 255, 0.07); }
}

@media (hover: hover) {
  .icon-btn:hover { background: rgba(255, 255, 255, 0.1); color: #ccc; }
}
</style>
