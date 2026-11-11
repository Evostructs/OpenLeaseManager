import { api } from './api.js'
import {
  buildScopedApiUrl,
  onPropertyScopeContextChange,
  propertyScopeNamespace
} from './propertyScope.js'

const DEFAULT_TTL_MS = 20000
const cache = new Map()

function clearCache() {
  cache.clear()
}

onPropertyScopeContextChange(() => {
  clearCache()
})

function isFresh(entry) {
  return entry && Object.prototype.hasOwnProperty.call(entry, 'value') && entry.expiresAt > Date.now()
}

export function cachedApi(path, options = {}) {
  const { force = false, ttlMs = DEFAULT_TTL_MS } = options
  const requestPath = buildScopedApiUrl(path)
  const namespace = propertyScopeNamespace()
  const cacheKey = `${namespace}\n${requestPath}`
  const existing = cache.get(cacheKey)

  if (!force) {
    if (isFresh(existing)) return Promise.resolve(existing.value)
    if (existing?.promise) return existing.promise
  }

  const request = api(requestPath)
    .then((value) => {
      if (cache.get(cacheKey)?.promise === request) {
        cache.set(cacheKey, { value, expiresAt: Date.now() + ttlMs, requestPath })
      }
      return value
    })
    .catch((error) => {
      if (cache.get(cacheKey)?.promise === request) cache.delete(cacheKey)
      throw error
    })

  cache.set(cacheKey, { promise: request, expiresAt: Date.now() + ttlMs, requestPath })
  return request
}

export function prefetchApi(path, options = {}) {
  return cachedApi(path, options)
}

export function invalidateApiCache(prefix = '') {
  if (!prefix) {
    clearCache()
    return
  }

  for (const [key, entry] of cache.entries()) {
    if (entry.requestPath?.startsWith(prefix)) cache.delete(key)
  }
}
