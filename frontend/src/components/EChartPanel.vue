<script setup>
import { locale, currencyUnit } from '../i18n/index.js'
import { localizeChartOption, restoreChartEvent } from '../i18n/charts.js'
import { beginReadyTask } from '../lib/operationTelemetry'
import { nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { cancelChartWork, loadChartRuntime, scheduleChartWork } from '../lib/chartRuntime'
import { THEME_CHANGE_EVENT, currentTheme, remapChartOptionForDark } from '../lib/theme'

const props = defineProps({
  option: {
    type: Object,
    default: () => ({})
  },
  height: {
    type: Number,
    default: 260
  },
  minimumPixelRatio: {
    type: Number,
    default: 2
  },
  empty: {
    type: Boolean,
    default: false
  },
  emptyText: {
    type: String,
    default: '暂无数据'
  },
  ariaLabel: {
    type: String,
    default: '数据图表'
  }
})
const emit = defineEmits(['chart-click', 'datazoom'])

const container = ref(null)
let chart = null
let chartTheme = ''
let finishChartRender = null
function renderedForTelemetry() { finishChartRender?.(); finishChartRender = null }
let chartPixelRatio = 0
let observer = null
let observedElement = null
let usingWindowResize = false
let pixelRatioQuery = null
let pointerActive = false
const workKey = Symbol('chart-panel')
let workGeneration = 0
let updateScheduled = false
let active = true
let mounted = false
let optionDirty = true
let resizeDirty = true
let lastWidth = 0
let lastHeight = 0

function disposeEchart() {
  renderedForTelemetry()
  if (chart) {
    chart.off('click', forwardChartClick)
    chart.off('datazoom', forwardDataZoom)
    chart.dispose()
    chart = null
    chartTheme = ''
    chartPixelRatio = 0
  }
}

function forwardChartClick(payload) {
  emit('chart-click', restoreChartEvent(payload, props.option))
}

function forwardDataZoom(payload) {
  emit('datazoom', payload)
}

function bindChartEvents() {
  if (!chart) return
  chart.off('click', forwardChartClick)
  chart.off('datazoom', forwardDataZoom)
  chart.on('click', forwardChartClick)
  chart.on('datazoom', forwardDataZoom)
  chart.off('finished', renderedForTelemetry)
  chart.on('finished', renderedForTelemetry)
}

function getDataURL(options = {}) {
  return chart?.getDataURL(options) || ''
}

function dispatchAction(payload) {
  if (!chart || !payload) return false
  chart.dispatchAction(payload)
  return true
}

function renderPixelRatio() {
  return Math.max(window.devicePixelRatio || 1, props.minimumPixelRatio)
}

function observePixelRatio() {
  if (pixelRatioQuery || typeof window.matchMedia !== 'function') return
  pixelRatioQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`)
  pixelRatioQuery.addEventListener?.('change', handlePixelRatioChange)
}

function stopObservingPixelRatio() {
  pixelRatioQuery?.removeEventListener?.('change', handlePixelRatioChange)
  pixelRatioQuery = null
}

function handlePixelRatioChange() {
  stopObservingPixelRatio()
  observePixelRatio()
  requestResize()
}

function beginPointerInteraction() {
  if (pointerActive) return
  pointerActive = true
  window.addEventListener('pointerup', finishPointerInteraction)
  window.addEventListener('pointercancel', finishPointerInteraction)
  window.addEventListener('blur', finishPointerInteraction)
}

function stopPointerInteraction() {
  pointerActive = false
  window.removeEventListener('pointerup', finishPointerInteraction)
  window.removeEventListener('pointercancel', finishPointerInteraction)
  window.removeEventListener('blur', finishPointerInteraction)
}

function finishPointerInteraction() {
  stopPointerInteraction()
  if (chart && chartPixelRatio !== renderPixelRatio()) requestResize()
}

function observeContainer() {
  observePixelRatio()
  if (typeof ResizeObserver !== 'undefined' && container.value) {
    if (!observer) {
      observer = new ResizeObserver(() => {
        requestResize()
      })
    }
    if (observedElement !== container.value) {
      if (observedElement) observer.unobserve(observedElement)
      observer.observe(container.value)
      observedElement = container.value
    }
    return
  }

  if (typeof window !== 'undefined' && !usingWindowResize) {
    window.addEventListener('resize', requestResize)
    usingWindowResize = true
  }
}

function canRender() {
  return mounted && active && !props.empty
    && (typeof document === 'undefined' || document.visibilityState !== 'hidden')
}

function requestResize() {
  resizeDirty = true
  scheduleUpdate()
}

function requestOptionUpdate() {
  optionDirty = true
  scheduleUpdate()
}

function updateChart(echarts, target) {
  if (chart && typeof chart.getDom === 'function' && chart.getDom() !== container.value) {
    disposeEchart()
  }
  const theme = currentTheme()
  if (chart && chartTheme !== theme) {
    disposeEchart()
  }
  const renderDpr = renderPixelRatio()
  let restoredOption
  if (chart && chartPixelRatio !== renderDpr && !pointerActive) {
    // 屏幕缩放改变时保留当前选区；已到达的新数据仍以调用方 option 为准。
    if (!optionDirty) restoredOption = chart.getOption()
    disposeEchart()
  }
  if (!chart) {
    // 默认保留大屏 zoom 所需的双倍采样；未缩放的驾驶舱租金图按原生像素绘制。
    chart = theme === 'dark'
      ? echarts.init(target, 'lease-dark', { devicePixelRatio: renderDpr })
      : echarts.init(target, null, { devicePixelRatio: renderDpr })
    chartTheme = theme
    chartPixelRatio = renderDpr
    bindChartEvents()
    if (restoredOption) {
      renderedForTelemetry()
      finishChartRender = beginReadyTask('chart-render')
      chart.setOption(restoredOption, true)
      optionDirty = false
    } else {
      optionDirty = true
    }
    lastWidth = target.clientWidth
    lastHeight = target.clientHeight
  }
  if (optionDirty || (resizeDirty && (lastWidth !== target.clientWidth || lastHeight !== target.clientHeight))) {
    renderedForTelemetry()
    finishChartRender = beginReadyTask('chart-render')
  }
  if (optionDirty) {
    optionDirty = false
    const localized = localizeChartOption(props.option, locale.value, currencyUnit.value)
    const option = theme === 'dark' ? remapChartOptionForDark(localized) : localized
    chart.setOption(option, true)
  }
  if (resizeDirty && (lastWidth !== target.clientWidth || lastHeight !== target.clientHeight)) {
    chart.resize()
    lastWidth = target.clientWidth
    lastHeight = target.clientHeight
  }
  resizeDirty = false
}

function scheduleUpdate() {
  if (!canRender() || updateScheduled) return
  updateScheduled = true
  const finishReady = beginReadyTask('chart-queue')
  const generation = workGeneration
  void nextTick(async () => {
    let rendered = false
    try {
      if (generation !== workGeneration || !canRender() || !container.value) return
      const target = container.value
      observeContainer()
      // 折叠区域等待尺寸通知，避免在零尺寸容器内初始化后再次完整绘图。
      if (!target.clientWidth || !target.clientHeight) return
      const echarts = await loadChartRuntime()
      if (generation !== workGeneration || !canRender() || container.value !== target) return
      await scheduleChartWork(() => {
        if (generation !== workGeneration || !canRender() || container.value !== target) return
        if (!target.clientWidth || !target.clientHeight) return
        updateChart(echarts, target)
        rendered = true
      }, workKey)
    } finally {
      finishReady()
      if (generation === workGeneration) {
        updateScheduled = false
        if (rendered && canRender() && container.value?.clientWidth && container.value?.clientHeight
          && (optionDirty || resizeDirty)) scheduleUpdate()
      }
    }
  })
}

function handleVisibilityChange() {
  if (typeof document !== 'undefined' && document.visibilityState === 'visible') {
    requestResize()
  } else {
    cancelPendingUpdate()
    stopPointerInteraction()
  }
}

function cancelPendingUpdate() {
  renderedForTelemetry()
  workGeneration += 1
  updateScheduled = false
  cancelChartWork(workKey)
}

function stopObserving() {
  stopObservingPixelRatio()
  stopPointerInteraction()
  if (observer) {
    observer.disconnect()
    observer = null
  }
  observedElement = null
  if (usingWindowResize) {
    window.removeEventListener('resize', requestResize)
    usingWindowResize = false
  }
}

function handleThemeChange() {
  requestOptionUpdate()
}

onMounted(() => {
  mounted = true
  scheduleUpdate()
  if (typeof window !== 'undefined') {
    window.addEventListener(THEME_CHANGE_EVENT, handleThemeChange)
  }
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handleVisibilityChange)
  }
})

onActivated(() => {
  active = true
  requestResize()
})

onDeactivated(() => {
  active = false
  cancelPendingUpdate()
  stopObserving()
})

watch(
  () => [props.option, props.empty, locale.value, currencyUnit.value],
  () => {
    if (props.empty) {
      cancelPendingUpdate()
      stopObserving()
      disposeEchart()
      return
    }
    requestOptionUpdate()
  },
  // 所有调用方都用 computed 生成新的 option；不遍历大型 series 数据树。
  { flush: 'post' }
)

watch(() => [props.height, props.minimumPixelRatio], requestResize, { flush: 'post' })

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener(THEME_CHANGE_EVENT, handleThemeChange)
  }
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  }
  mounted = false
  cancelPendingUpdate()
  stopObserving()
  disposeEchart()
})

defineExpose({ getDataURL, dispatchAction })
</script>

<template>
  <div v-if="empty" class="chart-empty" :style="{ height: `${height}px` }">
    {{ $t(emptyText) }}
  </div>
  <div
    v-else
    ref="container"
    class="chart-panel"
    role="img"
    :aria-label="$t(ariaLabel)"
    :style="{ height: `${height}px` }"
    @pointerdown="beginPointerInteraction"
  />
</template>
