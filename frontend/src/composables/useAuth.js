import { computed } from 'vue'

const TOKEN_KEY = 'token'

export function useAuth() {
  const isAuthenticated = computed(() => !!localStorage.getItem(TOKEN_KEY))

  function getToken() {
    return localStorage.getItem(TOKEN_KEY)
  }

  async function login(username, password) {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    })

    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error || 'Login failed')
    }

    const data = await res.json()
    localStorage.setItem(TOKEN_KEY, data.token)
    return data
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY)
  }

  return { login, logout, isAuthenticated, getToken }
}
