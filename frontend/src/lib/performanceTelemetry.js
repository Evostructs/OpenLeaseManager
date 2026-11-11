import { operationSnapshot, resetOperationTelemetry, safeEndpoint } from './operationTelemetry.js'
const MAX_ENTRIES = 80
const ROUTE_SAMPLE_DELAY_MS = 900

let started = false
const layoutShifts = []
const longTasks = []
const slowEvents = []
const routeSamples = []

function now() {
  if (typeof performance === 'undefined') return Date.now()
  return performance.now()
}

function keepRecent(list) {
  if (list.length > MAX_ENTRIES) list.splice(0, list.length - MAX_ENTRIES)
}

function supportedEntryTypes() {
  if (typeof PerformanceObserver === 'undefined') return []
  return PerformanceObserver.supportedEntryTypes || []
}

function observePerformance(type, callback, options = {}) {
  if (typeof PerformanceObserver === 'undefined') return
  if (!supportedEntryTypes().includes(type)) return
  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) callback(entry)
    })
    observer.observe({ type, buffered: true, ...options })
  } catch {
    // Browser support varies; telemetry must never affect app behavior.
  }
}

function sanitizeResourceName(name) {
  try {
    const url = new URL(name, window.location.origin)
    if (url.pathname.startsWith('/api/')) return safeEndpoint(url.pathname)
    return url.pathname.split('/').filter(Boolean).pop() || url.pathname
  } catch {
    return String(name || '').split('?')[0].split('/').pop() || ''
  }
}

function routeResources(startTime) {
  if (typeof performance === 'undefined') return []
  return performance.getEntriesByType('resource')
    .filter((entry) => entry.startTime >= startTime)
    .filter((entry) => ['script', 'fetch', 'xmlhttprequest'].includes(entry.initiatorType))
    .map((entry) => ({
      name: sanitizeResourceName(entry.name),
      type: entry.initiatorType === 'script' ? 'script' : 'api',
      duration: Math.round(entry.duration || 0),
      decodedBodySize: entry.decodedBodySize || 0,
      transferSize: entry.transferSize || 0
    }))
    .sort((a, b) => b.duration - a.duration)
    .slice(0, 8)
}

function summarizeSince(startTime) {
  const shifts = layoutShifts.filter((entry) => entry.startTime >= startTime)
  const tasks = longTasks.filter((entry) => entry.startTime >= startTime)
  const events = slowEvents.filter((entry) => entry.startTime >= startTime)
  return {
    cls: Number(shifts.reduce((sum, entry) => sum + entry.value, 0).toFixed(5)),
    maxShift: Number(Math.max(0, ...shifts.map((entry) => entry.value)).toFixed(5)),
    shiftCount: shifts.length,
    longTaskCount: tasks.length,
    maxLongTask: Math.round(Math.max(0, ...tasks.map((entry) => entry.duration))),
    longTaskTotal: Math.round(tasks.reduce((sum, entry) => sum + entry.duration, 0)),
    slowEventCount: events.length,
    maxEvent: Math.round(Math.max(0, ...events.map((entry) => entry.duration))),
    resources: routeResources(startTime)
  }
}

function finishRouteSample(sample) {
  routeSamples.push({
    routeKey: sample.routeKey,
    startedAt: Math.round(sample.startTime),
    observationWindowMs: Math.round(now() - sample.startTime),
    measurement: 'fixed-observation-window-not-readiness',
    ...summarizeSince(sample.startTime)
  })
  keepRecent(routeSamples)
}

function scheduleRouteSample(sample) {
  const finish = () => finishRouteSample(sample)
  const defer = () => window.setTimeout(finish, ROUTE_SAMPLE_DELAY_MS)
  if (typeof window.requestAnimationFrame === 'function') {
    window.requestAnimationFrame(() => window.requestAnimationFrame(defer))
    return
  }
  defer()
}

export function startPerformanceTelemetry() {
  if (started || typeof window === 'undefined') return
  started = true

  observePerformance('layout-shift', (entry) => {
    if (entry.hadRecentInput) return
    layoutShifts.push({ startTime: entry.startTime, value: entry.value || 0 })
    keepRecent(layoutShifts)
  })

  observePerformance('longtask', (entry) => {
    longTasks.push({ startTime: entry.startTime, duration: entry.duration || 0 })
    keepRecent(longTasks)
  })

  observePerformance('event', (entry) => {
    slowEvents.push({
      startTime: entry.startTime,
      duration: entry.duration || 0,
      name: entry.name || 'event'
    })
    keepRecent(slowEvents)
  }, { durationThreshold: 16 })

  window.__leaseManagerPerformance = {
    markRouteTransition,
    reset: resetPerformanceTelemetry,
    snapshot: performanceSnapshot
  }
}

export function markRouteTransition(routeKey) {
  if (typeof window === 'undefined') return
  startPerformanceTelemetry()
  scheduleRouteSample({ routeKey, startTime: now() })
}

export function resetPerformanceTelemetry() {
  resetOperationTelemetry()
  layoutShifts.length = 0
  longTasks.length = 0
  slowEvents.length = 0
  routeSamples.length = 0
}

export function performanceSnapshot() {
  return {
    ...operationSnapshot(),
    supported: supportedEntryTypes(),
    current: summarizeSince(0),
    routes: routeSamples.slice(-20),
    recentLongTasks: longTasks.slice(-10).map((entry) => ({
      startTime: Math.round(entry.startTime),
      duration: Math.round(entry.duration)
    })),
    recentEvents: slowEvents.slice(-10).map((entry) => ({
      name: entry.name,
      startTime: Math.round(entry.startTime),
      duration: Math.round(entry.duration)
    }))
  }
}
