import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  AriaComponent,
  AxisPointerComponent,
  DataZoomInsideComponent,
  DataZoomSliderComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

// Keep the chart runtime limited to the chart and component types used by the
// application. Importing the top-level `echarts` package pulls every renderer
// and chart type into the lazy chunk.
echarts.use([
  BarChart,
  LineChart,
  PieChart,
  AriaComponent,
  AxisPointerComponent,
  DataZoomInsideComponent,
  DataZoomSliderComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  CanvasRenderer
])

// 暗色图表主题：EChartPanel 根据 <html data-theme> 选择，并对视图层
// option 中硬编码的浅色值做颜色重映射（见 src/lib/theme.js）。
const darkAxis = {
  axisLine: { lineStyle: { color: '#475569' } },
  axisTick: { lineStyle: { color: '#475569' } },
  axisLabel: { color: '#94a3b8' },
  splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.16)' } }
}

echarts.registerTheme('lease-dark', {
  color: ['#60a5fa', '#34d399', '#fbbf24', '#f87171', '#a78bfa', '#22d3ee', '#f472b6', '#94a3b8'],
  backgroundColor: 'transparent',
  textStyle: { color: '#cbd5e1' },
  title: { textStyle: { color: '#e2e8f0' } },
  legend: { textStyle: { color: '#cbd5e1' } },
  tooltip: {
    backgroundColor: 'rgba(15, 23, 42, 0.94)',
    borderColor: 'rgba(148, 163, 184, 0.3)',
    textStyle: { color: '#e2e8f0' }
  },
  axisPointer: {
    lineStyle: { color: '#64748b' },
    label: { backgroundColor: '#334155', color: '#e2e8f0' }
  },
  categoryAxis: darkAxis,
  valueAxis: darkAxis,
  dataZoom: {
    backgroundColor: 'rgba(148, 163, 184, 0.12)',
    fillerColor: 'rgba(96, 165, 250, 0.18)',
    borderColor: 'rgba(148, 163, 184, 0.3)',
    handleStyle: { color: '#475569', borderColor: '#64748b' },
    moveHandleStyle: { color: '#475569' },
    emphasis: {
      handleStyle: { color: '#60a5fa', borderColor: '#93c5fd' },
      moveHandleStyle: { color: '#60a5fa' }
    },
    textStyle: { color: '#94a3b8' }
  }
})

export const init = echarts.init
