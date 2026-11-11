let chartRuntimePromise = null
let chartWorkScheduled = false
const chartWorkQueue = new Map()

export function loadChartRuntime() {
  if (!chartRuntimePromise) {
    chartRuntimePromise = import('./echartsRuntime')
  }
  return chartRuntimePromise
}

export function preloadChartRuntime() {
  return loadChartRuntime().then(() => true)
}

function runChartWorkNextFrame(callback) {
  if (typeof window === 'undefined') {
    queueMicrotask(callback)
    return
  }
  if (typeof window.requestAnimationFrame === 'function'
    && (typeof document === 'undefined' || document.visibilityState !== 'hidden')) {
    window.requestAnimationFrame(callback)
    return
  }
  window.setTimeout(callback, 0)
}

function scheduleNextChartWork() {
  if (chartWorkScheduled || !chartWorkQueue.size) return
  chartWorkScheduled = true
  runChartWorkNextFrame(() => {
    const next = chartWorkQueue.entries().next().value
    if (!next) {
      chartWorkScheduled = false
      return
    }
    const [key, job] = next
    chartWorkQueue.delete(key)
    Promise.resolve()
      .then(job.work)
      .then(job.resolve, job.reject)
      .finally(() => {
        chartWorkScheduled = false
        scheduleNextChartWork()
      })
  })
}

// 每张图仅保留一个待执行任务；不同图仍分帧执行，给输入和页面绘制让出时间。
export function scheduleChartWork(work, key = Symbol('chart-work')) {
  const pending = chartWorkQueue.get(key)
  if (pending) {
    pending.work = work
    return pending.promise
  }
  const job = { work }
  job.promise = new Promise((resolve, reject) => {
    Object.assign(job, { resolve, reject })
  })
  chartWorkQueue.set(key, job)
  scheduleNextChartWork()
  return job.promise
}

export function cancelChartWork(key) {
  const pending = chartWorkQueue.get(key)
  if (!pending) return
  chartWorkQueue.delete(key)
  pending.resolve()
}
