<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth.js'

const router = useRouter()
const { login } = useAuth()

const username = ref('')
const password = ref('')
const showPassword = ref(false)
const loading = ref(false)
const error = ref('')

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await login(username.value, password.value)
    router.push('/dashboard')
  } catch (e) {
    error.value = e.message || 'Login failed'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <q-card class="login-card" dark>
      <q-card-section>
        <div class="text-h5 text-center q-mb-md" style="color: #2ee8b7; font-weight: 700;">
          Smart Home
        </div>
      </q-card-section>

      <q-card-section>
        <q-banner v-if="error" class="q-mb-md bg-negative text-white" rounded>
          {{ error }}
        </q-banner>

        <q-form @submit.prevent="onSubmit" class="q-gutter-md">
          <q-input
            v-model="username"
            label="Имя пользователя"
            dark
            filled
            :disable="loading"
          >
            <template v-slot:prepend>
              <q-icon name="person" />
            </template>
          </q-input>

          <q-input
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            label="Пароль"
            dark
            filled
            :disable="loading"
          >
            <template v-slot:prepend>
              <q-icon name="lock" />
            </template>
            <template v-slot:append>
              <q-icon
                :name="showPassword ? 'visibility' : 'visibility_off'"
                class="cursor-pointer"
                @click="showPassword = !showPassword"
              />
            </template>
          </q-input>

          <q-btn
            type="submit"
            label="Войти"
            color="primary"
            class="full-width"
            size="lg"
            :loading="loading"
            :disable="loading"
            no-caps
          />
        </q-form>
      </q-card-section>
    </q-card>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: #111;
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: #1a1a2e;
  border-radius: 12px;
}
</style>
