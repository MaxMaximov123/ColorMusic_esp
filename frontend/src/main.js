import { createApp } from 'vue'
import { Quasar, Dark } from 'quasar'
import '@quasar/extras/material-icons/material-icons.css'
import 'quasar/src/css/index.sass'
import App from './App.vue'
import router from './router.js'
import './style.css'

const app = createApp(App)
app.use(Quasar, {
  plugins: {},
  config: {
    dark: true,
    brand: {
      primary: '#2ee8b7',
      secondary: '#1a1a2e',
      dark: '#111',
      'dark-page': '#111'
    }
  }
})
app.use(router)
app.mount('#app')
