// The caller owns this cache for one mounted cockpit and clears it on an
// authorization change. Explicit building keys never use the ambient scope.
export function createCockpitResourceCache({ ttlMs = 30000, maxEntries = 5, now = Date.now } = {}) {
  const entries = new Map()
  return {
    clear() { entries.clear() },
    read(key, load) {
      const existing = entries.get(key)
      if (existing?.promise) return existing.promise
      if (existing && existing.expiresAt > now()) return Promise.resolve(existing.value)
      const entry = {}
      const promise = Promise.resolve().then(load).then(value => {
        if (entries.get(key) === entry) {
          entry.value = value
          entry.expiresAt = now() + ttlMs
          delete entry.promise
        }
        return value
      }, error => {
        if (entries.get(key) === entry) entries.delete(key)
        throw error
      })
      entry.promise = promise
      entries.delete(key)
      entries.set(key, entry)
      while (entries.size > maxEntries) entries.delete(entries.keys().next().value)
      return promise
    }
  }
}

export function buildingAnalyticsSnapshot(snapshot, buildingCode = '') {
  if (snapshot?.scope?.scope_type !== 'building') return null
  const scopeCode = String(snapshot.scope.building?.code || snapshot.scope.scope_code || '').toLowerCase()
  if (!scopeCode || (buildingCode && scopeCode !== String(buildingCode).toLowerCase())) return null
  if (!snapshot.legacy_operations || !snapshot.legacy_contract_stats) return null
  const trend = snapshot.presentation?.trend ?? snapshot.legacy_operations.trend
  if (!Array.isArray(trend)) return null
  return { operations: snapshot.legacy_operations, trend }
}

// Live contract statistics already include every room's area, including vacant
// rooms and zero-area groups. Only older payloads need a separate rooms request.
export function hasAnalyticsRoomAreas(stats = {}) {
  const groups = stats.tower_data
  return Boolean(groups && !Array.isArray(groups) && typeof groups === 'object'
    && Object.values(groups).every(group => group
      && group.room_area !== null && group.room_area !== ''
      && Object.hasOwn(group, 'room_area') && Number.isFinite(Number(group.room_area))))
}
