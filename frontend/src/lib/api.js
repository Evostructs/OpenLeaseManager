export const AUTH_EXPIRED_EVENT = 'auth-expired'
export const AUTH_EXPIRED_MESSAGE = '登录已失效，请重新登录'
const AUTH_EXPIRED_STORAGE_KEY = 'lease-manager-auth-expired'

let lastAuthExpiredAt = 0

function emitAuthExpired(message) {
  const now = Date.now()
  if (now - lastAuthExpiredAt < 1000) return
  lastAuthExpiredAt = now
  const normalizedMessage = message || AUTH_EXPIRED_MESSAGE
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: { message: normalizedMessage } }))
  try {
    window.localStorage.setItem(
      AUTH_EXPIRED_STORAGE_KEY,
      JSON.stringify({ at: now, message: normalizedMessage })
    )
  } catch {
    // ignore storage failures; same-tab redirect still works
  }
}

export async function api(path, options = {}) {
  const { skipAuthRedirect = false, ...requestOptions } = options
  const config = {
    headers: {
      ...(requestOptions.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(requestOptions.headers || {})
    },
    credentials: 'include',
    ...requestOptions
  }

  const response = await fetch(path, config)
  if (response.status === 204) return null

  const contentType = response.headers.get('content-type') || ''
  const payload = contentType.includes('application/json') ? await response.json() : await response.text()

  if (!response.ok) {
    const message = typeof payload === 'string' ? payload : payload?.detail || payload?.message || '请求失败'
    if (response.status === 401 && !skipAuthRedirect) {
      emitAuthExpired(AUTH_EXPIRED_MESSAGE)
    }
    const error = new Error(message)
    error.status = response.status
    error.payload = payload
    throw error
  }

  return payload
}

export function openDownload(url) {
  window.open(url, '_blank', 'noopener,noreferrer')
}

export function readAuthExpiredBroadcast(event) {
  if (event.key !== AUTH_EXPIRED_STORAGE_KEY || !event.newValue) return null
  try {
    const payload = JSON.parse(event.newValue)
    return payload?.message || AUTH_EXPIRED_MESSAGE
  } catch {
    return AUTH_EXPIRED_MESSAGE
  }
}
