<script setup>
import * as echarts from 'echarts'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  option: {
    type: Object,
    default: () => ({})
  },
  height: {
    type: Number,
    default: 260
  },
  empty: {
    type: Boolean,
    default: false
  },
  emptyText: {
    type: String,
    default: '暂无数据'
  }
})

const container = ref(null)
let chart = null
let observer = null

function renderChart() {
  if (!container.value || props.empty) return
  if (!chart) {
    chart = echarts.init(container.value)
  }
  chart.setOption(props.option, true)
  chart.resize()
}

function disposeChart() {
  if (observer) {
    observer.disconnect()
    observer = null
  }
  if (chart) {
    chart.dispose()
    chart = null
  }
}

onMounted(async () => {
  await nextTick()
  if (typeof ResizeObserver !== 'undefined' && container.value) {
    observer = new ResizeObserver(() => {
      if (chart) chart.resize()
    })
    observer.observe(container.value)
  } else {
    window.addEventListener('resize', renderChart)
  }
  renderChart()
})

watch(
  () => [props.option, props.empty],
  async () => {
    await nextTick()
    if (props.empty) {
      if (chart) chart.clear()
      return
    }
    renderChart()
  },
  { deep: true }
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', renderChart)
  disposeChart()
})
</script>

<template>
  <div v-if="empty" class="chart-empty" :style="{ height: `${height}px` }">
    {{ emptyText }}
  </div>
  <div v-else ref="container" class="chart-panel" :style="{ height: `${height}px` }" />
</template>
