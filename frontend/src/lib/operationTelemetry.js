const MAX = 80
const kinds = new Set(['login', 'session-restore', 'building-switch', 'contract-filter', 'contract-list', 'receipt-save', 'financial-save'])
const statuses = new Set(['ok', 'error', 'degraded', 'superseded', 'uncertain', 'needs-review'])
export function safeEndpoint(path) {
  return String(path || '').split('?')[0].replace(/\/(?:\d+|[\da-f]{8}-[\da-f-]{27,})(?=\/|$)/gi, '/:id').slice(0,160)
}
export function createOperationTelemetry({ now, frame, hidden, timer = setTimeout, cancelTimer = clearTimeout, environment = () => ({}) }) {
  const pending = new Map(); const requests = []; const samples = []; let revision = 0; let nextId = 0
  const trim = list => { if (list.length > MAX) list.splice(0, list.length - MAX) }
  function task(type) {
    const id = ++nextId; pending.set(id, { type, start: now() }); let ended = false
    return () => { if (!ended) { ended = true; pending.delete(id) } }
  }
  function request(value) {
    requests.push({ endpoint: safeEndpoint(value.endpoint), start: value.start, duration: Math.max(0,value.duration), headersMs: value.headersMs ?? null, bodyMs: value.bodyMs ?? null, serverMs: value.serverMs ?? null, status: value.status || 0 })
    trim(requests)
  }
  function begin(kind, target = '') {
    const start = now(); const generation = revision; const context = environment(); let finished = false
    return { finish(status = 'ok') {
      if (finished) return
      finished = true
      const dataReady = now(); let done = false; let stableFrames = 0
      const complete = outcome => {
        if (done) return; done = true; cancelTimer(deadline)
        if (generation !== revision) return
        const calls = requests.filter(item => item.start >= start && item.start <= now())
        samples.push({ kind: kinds.has(kind) ? kind : 'other', target: /^[a-z-]{0,24}$/.test(target) ? target : '', status: outcome === 'ok' && calls.some(item => item.status === 0 || item.status >= 400) ? 'degraded' : outcome,
          environment: context, startedAt: start, dataReadyMs: dataReady - start, readyMs: now() - start, renderingAndDependenciesMs: now() - dataReady,
          requestCount: calls.length, requests: calls.map(item => ({...item})), pendingTypes: [...new Set([...pending.values()].map(item => item.type))] })
        trim(samples)
      }
      const deadline = timer(() => complete(hidden() ? 'background' : 'incomplete'), 30000)
      const check = () => {
        if (done) return
        if (hidden()) { complete('background'); return }
        if (pending.size) stableFrames = 0
        else stableFrames++
        if (stableFrames >= 2) { complete(statuses.has(status) ? status : 'error'); return }
        frame(check)
      }
      // Vue's queued DOM updates run before the frame checks. Requests, lazy
      // component loads and chart work started by that render extend readiness.
      Promise.resolve().then(() => frame(check))
    } }
  }
  return { task, request, begin, reset() { revision++; requests.length = 0; samples.length = 0 }, snapshot() { return { operations: samples.map(item => ({...item,requests:item.requests.map(row=>({...row}))})), pendingCount: pending.size } } }
}
const clock = () => typeof performance === 'undefined' ? Date.now() : performance.now()
const engine = createOperationTelemetry({ now: clock, hidden: () => typeof document !== 'undefined' && document.hidden,
  environment: () => typeof window === 'undefined' ? {} : ({ width: window.innerWidth, height: window.innerHeight, dpr: window.devicePixelRatio }),
  frame: callback => typeof requestAnimationFrame === 'function' ? requestAnimationFrame(callback) : setTimeout(callback,0) })
export const operationNow = clock
export const startOperation = (kind,target) => engine.begin(kind,target)
export const beginReadyTask = type => engine.task(type)
export const recordApiTiming = value => engine.request(value)
export const operationSnapshot = () => engine.snapshot()
export const resetOperationTelemetry = () => engine.reset()
export async function trackReadyWork(type, work) {
  const finish = beginReadyTask(type)
  try { return await work() } finally { finish() }
}
