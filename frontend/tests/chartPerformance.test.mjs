import * as i18n from '../src/i18n/index.js'
import * as chartI18n from '../src/i18n/charts.js'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import * as vue from 'vue'
import * as chartRuntime from '../src/lib/chartRuntime.js'
import * as theme from '../src/lib/theme.js'
import * as rentDomain from '../src/lib/rentAnalyticsDomain.js'
import * as format from '../src/lib/format.js'

// Execute the actual component setup and Vue lifecycle without adding a DOM library.
function componentSetup(filename, imports) {
  const { descriptor } = parse(readFileSync(new URL(`../src/components/${filename}`, import.meta.url), 'utf8'))
  const compiled = compileScript(descriptor, { id: filename }).content
    .replace(/import\s+([\s\S]*?)\s+from\s+(['"])(.*?)\2;?/g, (_, binding, _quote, path) => {
      const name = binding.trim().replace(/\s+as\s+/g, ': ')
      return `const ${name} = imports[${JSON.stringify(path)}];`
    })
    .replace('export default', 'return')
  return new Function('imports', compiled)({ vue, '../i18n/index.js': i18n, '../i18n/charts.js': chartI18n, '../lib/operationTelemetry': { beginReadyTask: () => () => {} }, ...imports })
}

function browserHarness(t) {
  const previous = { window: globalThis.window, document: globalThis.document, ResizeObserver: globalThis.ResizeObserver }
  const frames = []
  const timers = new Map()
  const observers = []
  const mediaQueries = []
  const cleanups = []
  let timerId = 0
  const events = () => {
    const handlers = new Map()
    return {
      addEventListener(name, handler) {
        if (!handlers.has(name)) handlers.set(name, new Set())
        handlers.get(name).add(handler)
      },
      removeEventListener(name, handler) { handlers.get(name)?.delete(handler) },
      listenerCount(name) { return handlers.get(name)?.size || 0 },
      fire(name, payload = {}) { for (const handler of handlers.get(name) || []) handler(payload) }
    }
  }
  globalThis.window = {
    ...events(), devicePixelRatio: 1,
    requestAnimationFrame(callback) { frames.push(callback) },
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id },
    clearTimeout(id) { timers.delete(id) },
    matchMedia(query) {
      const media = { ...events(), media: query, matches: false }
      mediaQueries.push(media)
      return media
    }
  }
  globalThis.document = { ...events(), visibilityState: 'visible', documentElement: { dataset: { theme: 'light' } } }
  globalThis.ResizeObserver = class {
    constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this) }
    observe(target) { this.target = target; this.disconnected = false }
    unobserve() {}
    disconnect() { this.disconnected = true }
  }
  t.after(() => {
    for (const cleanup of cleanups.reverse()) cleanup()
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete globalThis[name]
      else globalThis[name] = value
    }
  })
  return {
    frames, timers, observers, mediaQueries, cleanup(callback) { cleanups.push(callback) },
    async tick() { for (let i = 0; i < 12; i += 1) await vue.nextTick() },
    async frame() { frames.shift()?.(); for (let i = 0; i < 12; i += 1) await vue.nextTick() },
    fireTimers() {
      const pending = [...timers.values()]
      timers.clear()
      for (const timer of pending) timer.callback()
    }
  }
}

function mountSetup(component, props = {}, { keepAlive = false } = {}) {
  const node = (type, text = '') => ({ type, text, children: [], parent: null, clientWidth: 640, clientHeight: 260 })
  const renderer = vue.createRenderer({
    createElement: node, createText: text => node('text', text), createComment: text => node('comment', text),
    setText(target, text) { target.text = text }, setElementText(target, text) { target.text = text },
    parentNode: target => target.parent,
    nextSibling(target) { return target.parent?.children[target.parent.children.indexOf(target) + 1] || null },
    insert(target, parent, anchor) {
      if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1)
      const index = anchor ? parent.children.indexOf(anchor) : -1
      parent.children.splice(index < 0 ? parent.children.length : index, 0, target)
      target.parent = parent
    },
    remove(target) { target.parent?.children.splice(target.parent.children.indexOf(target), 1); target.parent = null },
    patchProp(target, key, _previous, value) {
      target[key] = value
      if (key === 'style' && value?.height) target.clientHeight = Number.parseInt(value.height, 10)
    }
  })
  let instance
  component.render = function () {
    const current = vue.getCurrentInstance()
    instance = current
    return current.props.empty ? vue.h('div') : vue.h('div', {
      ref: element => { if ('container' in current.setupState) current.setupState.container = element },
      style: { height: `${current.props.height || 260}px` }
    })
  }
  const currentProps = vue.shallowRef(props)
  const shown = vue.ref(true)
  const app = renderer.createApp({
    render() {
      const child = () => shown.value ? vue.h(component, currentProps.value) : vue.h('p')
      return keepAlive ? vue.h(vue.KeepAlive, null, { default: child }) : child()
    }
  })
  app.mount(node('root'))
  return { app, currentProps, shown, get instance() { return instance }, get state() { return instance.setupState } }
}

test('100 pending updates for one chart use one frame and only execute the latest work', async t => {
  const browser = browserHarness(t)
  const key = Symbol('same-chart')
  const executed = []
  const promises = Array.from({ length: 100 }, (_, index) => chartRuntime.scheduleChartWork(() => executed.push(index), key))
  assert.equal(new Set(promises).size, 1)
  assert.equal(browser.frames.length, 1)
  await browser.frame()
  await Promise.all(promises)
  assert.deepEqual(executed, [99])
  assert.equal(browser.frames.length, 0)
  assert.equal(browser.timers.size, 0)
})

test('cancelled charts take no queue slot; other charts render one per frame without a 32 ms idle gap', async t => {
  const browser = browserHarness(t)
  const cancelledKey = Symbol('cancelled-chart')
  const executed = []
  const cancelled = chartRuntime.scheduleChartWork(() => executed.push('cancelled'), cancelledKey)
  const first = chartRuntime.scheduleChartWork(() => executed.push('first'))
  const second = chartRuntime.scheduleChartWork(() => executed.push('second'))
  chartRuntime.cancelChartWork(cancelledKey)
  await cancelled
  await browser.frame()
  assert.deepEqual(executed, ['first'])
  assert.equal(browser.frames.length, 1)
  assert.equal(browser.timers.size, 0)
  await browser.frame()
  await Promise.all([first, second])
  assert.deepEqual(executed, ['first', 'second'])
})

test('chart mount/activation coalesce; resize, option, theme, empty and KeepAlive have separate work', async t => {
  const browser = browserHarness(t)
  const charts = []
  const runtime = {
    ...chartRuntime,
    async loadChartRuntime() {
      return { init(target, selectedTheme) {
        const chart = {
          target, selectedTheme, options: [], resizes: 0, events: new Map(), disposed: false,
          getDom() { return target }, setOption(option) { this.options.push(option) },
          resize() { this.resizes += 1 }, dispose() { this.disposed = true },
          off(name) { this.events.delete(name) }, on(name, handler) { this.events.set(name, handler) },
          getDataURL() { return 'data:image/png;base64,test' }, dispatchAction(payload) { this.action = payload }
        }
        charts.push(chart)
        return chart
      } }
    }
  }
  const component = componentSetup('EChartPanel.vue', { '../lib/chartRuntime': runtime, '../lib/theme': theme })
  const clicks = []
  let seriesReads = 0
  const initialOption = { series: [{ get data() { seriesReads += 1; return [1] } }] }
  const mounted = mountSetup(component, { option: initialOption, onChartClick: value => clicks.push(value) }, { keepAlive: true })
  browser.cleanup(() => mounted.app.unmount())
  await browser.tick()
  browser.observers[0].callback()
  await browser.tick()
  assert.equal(browser.frames.length, 1, 'mount, activate and initial ResizeObserver share a frame')
  await browser.frame()
  assert.equal(charts.length, 1)
  assert.equal(charts[0].options.length, 1)
  assert.equal(seriesReads, 0, 'the component watcher must not traverse the series data tree')
  assert.equal(charts[0].resizes, 0, 'initialization already has the correct dimensions')

  charts[0].target.clientWidth = 800
  for (let i = 0; i < 10; i += 1) browser.observers[0].callback()
  await browser.tick()
  await browser.frame()
  assert.equal(charts[0].resizes, 1)
  assert.equal(charts[0].options.length, 1, 'resize must not replace chart data')
  for (let i = 2; i <= 11; i += 1) {
    mounted.currentProps.value = { ...mounted.currentProps.value, option: { series: [{ data: [i] }] } }
    await browser.tick()
  }
  assert.equal(browser.frames.length, 1)
  await browser.frame()
  assert.equal(charts[0].options.length, 2, 'ten data updates become one setOption')
  assert.deepEqual(charts[0].options.at(-1).series[0].data, [11])
  charts[0].events.get('click')({ name: 'test' })
  assert.deepEqual(clicks, [{ name: 'test' }])
  assert.equal(mounted.instance.exposed.getDataURL(), 'data:image/png;base64,test')
  assert.equal(mounted.instance.exposed.dispatchAction({ type: 'dataZoom' }), true)

  mounted.currentProps.value = { ...mounted.currentProps.value, option: { series: [{ data: [12] }] } }
  await browser.tick()
  mounted.shown.value = false
  await browser.tick()
  await browser.frame()
  assert.equal(charts[0].options.length, 2, 'deactivation cancels already queued updates')
  assert.equal(browser.observers[0].disconnected, true)
  mounted.currentProps.value = { ...mounted.currentProps.value, option: { series: [{ data: [13] }] } }
  await browser.tick()
  assert.equal(browser.frames.length, 0)
  mounted.shown.value = true
  await browser.tick()
  await browser.frame()
  assert.equal(charts[0].options.length, 3)
  assert.deepEqual(charts[0].options.at(-1).series[0].data, [13])

  document.documentElement.dataset.theme = 'dark'
  window.fire(theme.THEME_CHANGE_EVENT)
  await browser.tick()
  await browser.frame()
  assert.equal(charts[0].disposed, true)
  assert.equal(charts[1].selectedTheme, 'lease-dark')
  assert.equal(charts[1].options.length, 1)
  assert.deepEqual([...charts[1].events.keys()].sort(), ['click', 'datazoom', 'finished'])
  mounted.currentProps.value = { ...mounted.currentProps.value, empty: true }
  await browser.tick()
  assert.equal(charts[1].disposed, true)
  mounted.currentProps.value = { ...mounted.currentProps.value, empty: false }
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 3)
  assert.equal(charts[2].options.length, 1)

  mounted.currentProps.value = { ...mounted.currentProps.value, height: 330 }
  await browser.tick()
  await browser.frame()
  assert.equal(charts[2].resizes, 1)
  assert.equal(charts[2].options.length, 1, 'height changes only resize the chart')
  document.visibilityState = 'hidden'
  document.fire('visibilitychange')
  mounted.currentProps.value = { ...mounted.currentProps.value, option: { series: [{ data: [14] }] } }
  await browser.tick()
  assert.equal(browser.frames.length, 0, 'hidden tabs retain dirty state without doing chart work')
  document.visibilityState = 'visible'
  document.fire('visibilitychange')
  await browser.tick()
  await browser.frame()
  assert.equal(charts[2].options.length, 2)
  assert.deepEqual(charts[2].options.at(-1).series[0].data, [14])
})

function pixelRatioChartHarness(t, props, nativePixelRatio = 1) {
  const browser = browserHarness(t)
  window.devicePixelRatio = nativePixelRatio
  const charts = []
  const runtime = {
    ...chartRuntime,
    async loadChartRuntime() {
      return { init(target, selectedTheme, settings) {
        const chart = {
          target, selectedTheme, settings, options: [], resizes: 0, optionReads: 0, events: new Map(), disposed: false,
          getDom() { return target },
          getOption() { this.optionReads += 1; return this.currentOption },
          setOption(option) { this.currentOption = option; this.options.push(option) },
          resize() { this.resizes += 1 }, dispose() { this.disposed = true },
          off(name) { this.events.delete(name) }, on(name, handler) { this.events.set(name, handler) }
        }
        charts.push(chart)
        return chart
      } }
    }
  }
  const component = componentSetup('EChartPanel.vue', { '../lib/chartRuntime': runtime, '../lib/theme': theme })
  const mounted = mountSetup(component, props, { keepAlive: true })
  browser.cleanup(() => mounted.app.unmount())
  return { browser, mounted, charts }
}

test('chart sampling defaults to 2, allows native-resolution opt-in, and retains HiDPI resolution', async t => {
  const cases = [
    { native: 1, minimum: undefined, expected: 2 },
    { native: 1.5, minimum: undefined, expected: 2 },
    { native: 1, minimum: 1, expected: 1 },
    { native: 1.5, minimum: 1, expected: 1.5 },
    { native: 2, minimum: 1, expected: 2 },
    { native: 3, minimum: 1, expected: 3 }
  ]
  for (const sample of cases) {
    await t.test(`native ${sample.native}, minimum ${sample.minimum ?? 'default'}`, async child => {
      const { browser, charts } = pixelRatioChartHarness(child, {
        option: { series: [{ data: [1, 2] }] }, minimumPixelRatio: sample.minimum
      }, sample.native)
      await browser.tick()
      await browser.frame()
      assert.equal(charts.length, 1)
      assert.equal(charts[0].settings.devicePixelRatio, sample.expected)
      assert.equal(charts[0].options.length, 1)
      assert.equal(charts[0].optionReads, 0)
    })
  }
})

test('DPR changes rebuild once with the current selection and events; ordinary resize keeps the chart', async t => {
  const zooms = []
  const { browser, charts } = pixelRatioChartHarness(t, {
    minimumPixelRatio: 1,
    option: { series: [{ data: [1, 2, 3] }], dataZoom: [{ start: 0, end: 100 }] },
    onDatazoom: payload => zooms.push(payload)
  })
  await browser.tick()
  await browser.frame()
  const selectedOption = { ...charts[0].currentOption, dataZoom: [{ start: 30, end: 60 }] }
  charts[0].currentOption = selectedOption
  window.devicePixelRatio = 1.5
  browser.mediaQueries.at(-1).fire('change')
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 2)
  assert.equal(charts[0].disposed, true)
  assert.equal(charts[0].optionReads, 1)
  assert.equal(charts[1].settings.devicePixelRatio, 1.5)
  assert.deepEqual(charts[1].options, [selectedOption], 'restored selection must not be replaced by stale props')
  assert.deepEqual([...charts[1].events.keys()].sort(), ['click', 'datazoom', 'finished'])
  charts[1].events.get('datazoom')({ start: 40, end: 70 })
  assert.deepEqual(zooms, [{ start: 40, end: 70 }])
  assert.equal(browser.mediaQueries.at(-1).media, '(resolution: 1.5dppx)')

  charts[1].target.clientWidth = 900
  browser.observers[0].callback()
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 2)
  assert.equal(charts[1].resizes, 1)
  assert.equal(charts[1].options.length, 1)
  assert.equal(charts[1].optionReads, 0, 'ordinary resize must not inspect or replace chart data')
})

test('DPR rebuild uses pending application data instead of restoring an older chart option', async t => {
  const { browser, charts, mounted } = pixelRatioChartHarness(t, {
    minimumPixelRatio: 1, option: { series: [{ data: [1] }], dataZoom: [{ start: 30, end: 60 }] }
  })
  await browser.tick()
  await browser.frame()
  const latestOption = { series: [{ data: [2, 3] }], dataZoom: [{ start: 0, end: 100 }] }
  window.devicePixelRatio = 2
  browser.observers[0].callback()
  mounted.currentProps.value = { ...mounted.currentProps.value, option: latestOption }
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 2)
  assert.equal(charts[0].optionReads, 0)
  assert.deepEqual(charts[1].options, [latestOption])
})

test('DPR rebuild waits for pointer release and rechecks after KeepAlive activation', async t => {
  const { browser, charts, mounted } = pixelRatioChartHarness(t, {
    minimumPixelRatio: 1, option: { series: [{ data: [1, 2] }] }
  })
  await browser.tick()
  await browser.frame()
  mounted.state.beginPointerInteraction()
  window.devicePixelRatio = 1.5
  browser.mediaQueries.at(-1).fire('change')
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 1, 'screen changes must not interrupt a held drag')
  const selectedOption = { ...charts[0].currentOption, dataZoom: [{ start: 25, end: 75 }] }
  charts[0].currentOption = selectedOption
  window.fire('pointerup')
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 2)
  assert.deepEqual(charts[1].options, [selectedOption])

  mounted.shown.value = false
  await browser.tick()
  window.devicePixelRatio = 2
  mounted.shown.value = true
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 3)
  assert.equal(charts[2].settings.devicePixelRatio, 2)
  assert.deepEqual(charts[2].options, [selectedOption])
})

test('window blur releases a held drag and removes its temporary listeners before DPR rebuild', async t => {
  const { browser, charts, mounted } = pixelRatioChartHarness(t, {
    minimumPixelRatio: 1, option: { series: [{ data: [1, 2] }] }
  })
  await browser.tick()
  await browser.frame()
  mounted.state.beginPointerInteraction()
  mounted.state.beginPointerInteraction()
  for (const event of ['pointerup', 'pointercancel', 'blur']) assert.equal(window.listenerCount(event), 1)
  window.devicePixelRatio = 1.5
  browser.mediaQueries.at(-1).fire('change')
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 1)
  window.fire('blur')
  for (const event of ['pointerup', 'pointercancel', 'blur']) assert.equal(window.listenerCount(event), 0)
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 2, 'a pointer released outside the visible window must not block DPR updates')
  assert.equal(charts[1].settings.devicePixelRatio, 1.5)
  window.devicePixelRatio = 2
  window.fire('blur')
  await browser.tick()
  assert.equal(browser.frames.length, 0, 'blur listener must not remain after the interaction ends')
  browser.mediaQueries.at(-1).fire('change')
  await browser.tick()
  await browser.frame()
  assert.equal(charts.length, 3)
  assert.equal(charts[2].settings.devicePixelRatio, 2)
  mounted.state.beginPointerInteraction()
  mounted.shown.value = false
  await browser.tick()
  for (const event of ['pointerup', 'pointercancel', 'blur']) assert.equal(window.listenerCount(event), 0)
})

function analyticsPayload() {
  const dates = Array.from({ length: 20 }, (_, index) => `2026-09-${String(index + 1).padStart(2, '0')}`)
  return {
    bounds: { min_date: dates[0], max_date: dates.at(-1), today: dates.at(-1), default_start: dates[10], default_end: dates.at(-1) },
    segments: [{ key: 'overall', label: '整体' }],
    coverage: { total_contracts: 1, included_contracts: 1, excluded_contracts: 0, coverage_rate: 100, excluded: [] },
    daily: dates.map((date, index) => ({ date, phase: 'history', segments: { overall: { rent: (index + 1) * 10, area: 10, effective_daily_rent: index + 1 } } }))
  }
}

test('rent drag updates live summaries but retains the chart option until release, including comparison curves', async t => {
  const browser = browserHarness(t)
  let aggregationCount = 0
  const component = componentSetup('RentTimelineAnalytics.vue', {
    './ChartSkeleton.vue': {}, './EChartPanel.vue': {},
    '../lib/api': { api: async () => analyticsPayload(), openDownload() {} },
    '../lib/format': format,
    '../lib/cockpitDomain.js': { cockpitBuildingPath: path => path },
    '../lib/rentAnalyticsDomain': { ...rentDomain, aggregateRentTimeline(...args) { aggregationCount += 1; return rentDomain.aggregateRentTimeline(...args) } }
  })
  const mounted = mountSetup(component)
  browser.cleanup(() => mounted.app.unmount())
  await browser.tick()
  assert.equal(vue.isReactive(mounted.state.analytics), false, 'analytics snapshots should not be deep proxied')
  mounted.state.comparisonMode = 'previous'
  const originalOption = mounted.state.chartOption
  const originalSummary = mounted.state.liveSummary.overall.total.rent
  assert.equal(aggregationCount, 1)
  mounted.state.beginZoomInteraction()
  for (let index = 0; index < 10; index += 1) {
    mounted.state.handleDataZoom({ startValue: '2026-09-12', endValue: `2026-09-${15 + index % 5}` })
    await browser.tick()
    assert.equal(mounted.state.chartOption, originalOption, 'preview must retain all series and zoom configuration')
  }
  assert.equal(aggregationCount, 1)
  assert.notEqual(mounted.state.liveSummary.overall.total.rent, originalSummary)
  assert.equal(mounted.state.draftStartDate, '2026-09-12')
  assert.equal(mounted.state.startDate, '2026-09-11')
  assert.equal(browser.timers.size, 0, 'a held pointer must not commit during a 240 ms pause')
  window.fire('pointerup')
  await browser.tick()
  assert.equal(mounted.state.startDate, '2026-09-12')
  assert.equal(mounted.state.endDate, '2026-09-19')
  assert.notEqual(mounted.state.chartOption, originalOption)
  assert.equal(aggregationCount, 2, 'release performs exactly one aggregation')
  assert.equal(mounted.state.chartOption.series.find(row => row.name === '前一等长区间').data.length > 0, true)

  const committedOption = mounted.state.chartOption
  mounted.state.handleDataZoom({ startValue: '2026-09-12', endValue: '2026-09-19' })
  assert.equal(mounted.state.chartOption, committedOption, 'a trailing release event for the same range is a no-op')
  assert.equal(browser.timers.size, 0)

  mounted.state.handleDataZoom({ startValue: '2026-09-13', endValue: '2026-09-18' })
  assert.equal(browser.timers.size, 1, 'wheel zoom retains the debounce fallback')
  assert.equal(mounted.state.chartOption, committedOption)
  browser.fireTimers()
  await browser.tick()
  assert.equal(mounted.state.startDate, '2026-09-13')
  assert.equal(mounted.state.endDate, '2026-09-18')
  assert.notEqual(mounted.state.chartOption, committedOption)
  assert.equal(aggregationCount, 3)

  mounted.state.handleDataZoom({ startValue: '2026-09-14', endValue: '2026-09-17' })
  mounted.state.applyRange('2026-09-11', '2026-09-20')
  assert.equal(browser.timers.size, 0, 'a manual date change cancels stale pending zoom work')
  browser.fireTimers()
  assert.equal(mounted.state.startDate, '2026-09-11')
  assert.equal(mounted.state.endDate, '2026-09-20')
})
