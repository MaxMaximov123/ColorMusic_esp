import { useAuth } from './useAuth.js'

export function useApi() {
  const { getToken } = useAuth()

  async function fetchTemperatureHistory(deviceId, hours) {
    const token = getToken()
    const res = await fetch(`/api/temperature/${deviceId}?hours=${hours}`, {
      headers: {
        Authorization: `Bearer ${token}`
      }
    })

    if (!res.ok) {
      throw new Error('Failed to fetch temperature history')
    }

    return await res.json()
  }

  return { fetchTemperatureHistory }
}
