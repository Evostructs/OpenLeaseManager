<script setup>
import { computed, onMounted, ref } from 'vue'
import EChartPanel from '../components/EChartPanel.vue'
import { api } from '../lib/api'
import { formatArea, formatCurrency, formatNumber } from '../lib/format'

const props = defineProps({
  stats: {
    type: Object,
    default: () => ({})
  }
})

const statusOrder = ['在租', '即将到期', '待生效', '已退租', '逾期占用']
const statusPalette = {
  在租: { color: '#3b82f6', bg: '#dbeafe' },
  即将到期: { color: '#d97706', bg: '#fef3c7' },
  待生效: { color: '#10b981', bg: '#d1fae5' },
  已退租: { color: '#64748b', bg: '#e2e8f0' },
  逾期占用: { color: '#dc2626', bg: '#fee2e2' }
}

const towerRoomArea = ref({})

function roundTo(value, digits = 0) {
  const factor = 10 ** digits
  return Math.round(Number(value || 0) * factor) / factor
}

function shortValue(value, unit = '') {
  const numeric = Number(value || 0)
  if (Math.abs(numeric) >= 10000) return `${roundTo(numeric / 10000, 1)}万${unit}`
  return `${formatNumber(roundTo(numeric, 0))}${unit}`
}

function formatPercentDiff(value) {
  const numeric = Number(value || 0)
  const prefix = numeric > 0 ? '+' : ''
  return `${prefix}${roundTo(numeric, 1)}%`
}

function formatPointDiff(value) {
  const numeric = Number(value || 0)
  const prefix = numeric > 0 ? '+' : ''
  return `${prefix}${roundTo(numeric, 1)} 个百分点`
}

const metrics = computed(() => props.stats.metrics || {})
const trends = computed(() => props.stats.metrics_trends || {})

const leasedContractCount = computed(() => {
  const statuses = props.stats.statuses || {}
  return Number(statuses.在租 || 0) + Number(statuses.即将到期 || 0) + Number(statuses.逾期占用 || 0)
})

const kpis = computed(() => {
  const totalContracts = Number(props.stats.total || 0)
  const currentArea = Number(metrics.value.total_area || 0)
  const currentRent = Number(metrics.value.total_monthly_rent || 0)
  const currentOccupancy = Number(metrics.value.occupancy_rate || 0)
  const contractShare = totalContracts ? Math.round((leasedContractCount.value / totalContracts) * 100) : 0

  return [
    {
      key: 'area',
      label: '在租面积',
      value: `${formatArea(currentArea)} ㎡`,
      helper: `环比 ${formatPercentDiff(trends.value.area?.mom)} · 同比 ${formatPercentDiff(trends.value.area?.yoy)}`
    },
    {
      key: 'occupancy',
      label: '出租率',
      value: `${formatNumber(currentOccupancy)}%`,
      helper: `环比 ${formatPointDiff(trends.value.occupancy_rate?.mom)} · 同比 ${formatPointDiff(trends.value.occupancy_rate?.yoy)}`
    },
    {
      key: 'contracts',
      label: '执行中合同',
      value: formatNumber(leasedContractCount.value),
      helper: `占全部合同 ${contractShare}%`
    },
    {
      key: 'rent',
      label: '月租金',
      value: formatCurrency(currentRent),
      helper: `环比 ${formatPercentDiff(trends.value.rent?.mom)} · 同比 ${formatPercentDiff(trends.value.rent?.yoy)}`
    }
  ]
})

const statusRows = computed(() => {
  const entries = Object.entries(props.stats.statuses || {})
  const total = entries.reduce((sum, [, count]) => sum + Number(count || 0), 0)
  return entries
    .map(([label, count]) => ({
      label,
      count: Number(count || 0),
      percent: total ? Math.round((Number(count || 0) / total) * 100) : 0,
      ...(statusPalette[label] || { color: '#94a3b8', bg: '#e5e7eb' }),
      order: statusOrder.indexOf(label) === -1 ? 999 : statusOrder.indexOf(label)
    }))
    .sort((a, b) => a.order - b.order || b.count - a.count)
})

const statusOption = computed(() => ({
  tooltip: {
    trigger: 'item',
    formatter: ({ name, value, percent }) => `${name}<br/>数量：${formatNumber(value)}<br/>占比：${percent}%`
  },
  series: [
    {
      type: 'pie',
      radius: ['56%', '76%'],
      center: ['42%', '54%'],
      avoidLabelOverlap: true,
      label: { show: false },
      itemStyle: { borderColor: '#ffffff', borderWidth: 4 },
      data: statusRows.value.map((item) => ({
        name: item.label,
        value: item.count,
        itemStyle: { color: item.color }
      }))
    }
  ]
}))

const towerRows = computed(() => {
  const totalRent = Object.values(props.stats.tower_data || {}).reduce((sum, item) => sum + Number(item.rent || 0), 0)
  return Object.entries(props.stats.tower_data || {})
    .map(([tower, item]) => {
      const area = Number(item.area || 0)
      const rent = Number(item.rent || 0)
      const roomArea = Number(towerRoomArea.value[tower] || 0)
      const occupancyRate = roomArea > 0 ? roundTo((area / roomArea) * 100, 1) : 0
      return {
        tower,
        count: Number(item.count || 0),
        area,
        roomArea,
        vacantArea: Math.max(roomArea - area, 0),
        rent,
        rentWan: roundTo(rent / 10000, 1),
        avgDailyRent: area > 0 ? roundTo(rent / area / 30, 2) : 0,
        occupancyRate,
        revenueShare: totalRent > 0 ? roundTo((rent / totalRent) * 100, 1) : 0,
        avgContractArea: item.count ? roundTo(area / item.count, 0) : 0
      }
    })
    .sort((a, b) => b.rent - a.rent || b.area - a.area || a.tower.localeCompare(b.tower, 'zh-CN'))
})

const towerMixOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (params) => {
      const leased = params.find((item) => item.seriesName === '已租面积')
      const vacant = params.find((item) => item.seriesName === '待租面积')
      const rate = params.find((item) => item.seriesName === '出租率')
      return [
        params[0]?.name || '',
        `已租面积：${formatArea(leased?.value || 0)} ㎡`,
        `待租面积：${formatArea(vacant?.value || 0)} ㎡`,
        `出租率：${rate?.value || 0}%`
      ].join('<br/>')
    }
  },
  legend: {
    top: 0,
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: '#6b7280', fontSize: 12 }
  },
  grid: { left: 64, right: 132, top: 44, bottom: 40, containLabel: true },
  xAxis: [
    {
      type: 'value',
      name: '面积（㎡）',
      nameLocation: 'middle',
      nameGap: 22,
      splitLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: {
        color: '#6b7280',
        formatter: (value) => shortValue(value)
      },
      nameTextStyle: { color: '#6b7280' }
    },
    {
      type: 'value',
      name: '出租率（%）',
      nameLocation: 'middle',
      nameGap: 22,
      min: 0,
      max: 100,
      splitLine: { show: false },
      axisLabel: { color: '#6b7280' },
      nameTextStyle: { color: '#6b7280' }
    }
  ],
  yAxis: {
    type: 'category',
    data: towerRows.value.map((item) => item.tower),
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: { color: '#374151' }
  },
  series: [
    {
      name: '已租面积',
      type: 'bar',
      stack: 'area',
      barWidth: 16,
      itemStyle: { color: '#3b82f6', borderRadius: [0, 8, 8, 0] },
      data: towerRows.value.map((item) => item.area)
    },
    {
      name: '待租面积',
      type: 'bar',
      stack: 'area',
      barWidth: 16,
      itemStyle: { color: '#dbeafe', borderRadius: [0, 8, 8, 0] },
      data: towerRows.value.map((item) => item.vacantArea)
    },
    {
      name: '出租率',
      type: 'line',
      xAxisIndex: 1,
      smooth: true,
      symbol: 'circle',
      symbolSize: 7,
      lineStyle: { width: 3, color: '#0f766e' },
      itemStyle: { color: '#0f766e' },
      data: towerRows.value.map((item) => item.occupancyRate)
    }
  ]
}))

const towerRentOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'line' },
    formatter: (params) => {
      const daily = params.find((item) => item.seriesName === '平均日租')
      const monthly = params.find((item) => item.seriesName === '月租金')
      return [
        params[0]?.axisValue || '',
        `平均日租：${formatCurrency(daily?.value || 0)} 元/㎡/天`,
        `月租金：${formatCurrency(monthly?.value || 0)} 万元/月`
      ].join('<br/>')
    }
  },
  legend: {
    top: 0,
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: '#6b7280', fontSize: 12 }
  },
  grid: { left: 60, right: 72, top: 68, bottom: 36, containLabel: true },
  graphic: [
    {
      type: 'text',
      left: 60,
      top: 34,
      style: {
        text: '平均日租（元/㎡/天）',
        fill: '#6b7280',
        fontSize: 12,
        fontWeight: 600
      }
    },
    {
      type: 'text',
      right: 72,
      top: 34,
      style: {
        text: '月租金（万元/月）',
        fill: '#6b7280',
        fontSize: 12,
        fontWeight: 600,
        textAlign: 'right'
      }
    }
  ],
  xAxis: {
    type: 'category',
    data: towerRows.value.map((item) => item.tower),
    axisLine: { lineStyle: { color: '#d1d5db' } },
    axisLabel: { color: '#374151' }
  },
  yAxis: [
    {
      type: 'value',
      name: '平均日租（元/㎡/天）',
      nameLocation: 'middle',
      nameGap: 16,
      min: 0,
      max: 6,
      splitLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: { color: '#6b7280' },
      nameTextStyle: { color: 'transparent', fontSize: 0 }
    },
    {
      type: 'value',
      name: '月租金（万元/月）',
      nameLocation: 'middle',
      nameGap: 16,
      splitLine: { show: false },
      axisLabel: { color: '#6b7280' },
      nameTextStyle: { color: 'transparent', fontSize: 0 }
    }
  ],
  series: [
    {
      name: '平均日租',
      type: 'bar',
      barMaxWidth: 34,
      itemStyle: { color: '#60a5fa', borderRadius: [8, 8, 0, 0] },
      data: towerRows.value.map((item) => item.avgDailyRent)
    },
    {
      name: '月租金',
      type: 'line',
      yAxisIndex: 1,
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { width: 3, color: '#1d4ed8' },
      itemStyle: { color: '#1d4ed8' },
      label: {
        show: true,
        position: 'top',
        color: '#1d4ed8',
        fontSize: 11,
        formatter: ({ value }) => `${value} 万`
      },
      data: towerRows.value.map((item) => item.rentWan)
    }
  ]
}))

const alerts = computed(() => (props.stats.expiring_soon || []).slice(0, 6))

const expirySummary = computed(() => {
  const urgent = alerts.value.filter((item) => item.days_left <= 30).length
  const watch = alerts.value.filter((item) => item.days_left > 30).length
  return { urgent, watch }
})

const alertHighlights = computed(() => {
  const leaseTypes = props.stats.lease_types || {}
  const dominantLeaseType = Object.entries(leaseTypes).sort((a, b) => Number(b[1]) - Number(a[1]))[0]?.[0] || '--'
  return [
    { label: '保证金存量', value: formatCurrency(metrics.value.total_deposit || 0) },
    { label: '主力签约类型', value: dominantLeaseType }
  ]
})

const forecastRows = computed(() => props.stats.income_forecast || [])

const forecastOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    formatter: (params) => {
      const bar = params.find((item) => item.seriesName === '月租金预测')
      const line = params.find((item) => item.seriesName === '趋势线')
      return [
        params[0]?.axisValue || '',
        `月租金预测：${formatCurrency(bar?.value || 0)}`,
        `趋势线：${formatCurrency(line?.value || 0)}`
      ].join('<br/>')
    }
  },
  legend: {
    top: 0,
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: '#6b7280', fontSize: 12 }
  },
  grid: { left: 64, right: 28, top: 44, bottom: 32, containLabel: true },
  xAxis: {
    type: 'category',
    data: forecastRows.value.map((item) => item.month),
    axisLabel: { color: '#6b7280' },
    axisLine: { lineStyle: { color: '#d1d5db' } }
  },
  yAxis: {
    type: 'value',
    name: '月租金（元）',
    nameGap: 18,
    splitLine: { lineStyle: { color: '#e5e7eb' } },
    axisLabel: {
      color: '#6b7280',
      formatter: (value) => shortValue(value)
    },
    nameTextStyle: { color: '#6b7280' }
  },
  series: [
    {
      name: '月租金预测',
      type: 'bar',
      barMaxWidth: 28,
      itemStyle: { color: '#bfdbfe', borderRadius: [8, 8, 0, 0] },
      data: forecastRows.value.map((item) => item.income)
    },
    {
      name: '趋势线',
      type: 'line',
      smooth: true,
      symbol: 'circle',
      symbolSize: 7,
      lineStyle: { width: 3, color: '#2563eb' },
      itemStyle: { color: '#2563eb' },
      label: {
        show: true,
        formatter: ({ value }) => shortValue(value),
        color: '#1d4ed8'
      },
      data: forecastRows.value.map((item) => item.income)
    }
  ]
}))

async function fetchTowerRoomArea() {
  try {
    const rooms = await api('/api/rooms')
    const summary = {}
    for (const room of rooms) {
      if (!room?.tower) continue
      summary[room.tower] = (summary[room.tower] || 0) + Number(room.area || 0)
    }
    towerRoomArea.value = summary
  } catch {
    towerRoomArea.value = {}
  }
}

onMounted(fetchTowerRoomArea)
</script>

<template>
  <section class="content dashboard-page">
    <div class="kpi-grid">
      <article v-for="item in kpis" :key="item.key" class="card kpi-card">
        <div class="section-label">{{ item.label }}</div>
        <h3>{{ item.value }}</h3>
        <p>{{ item.helper }}</p>
      </article>
    </div>

    <div class="dashboard-grid">
      <article class="card dashboard-card">
        <div class="card-header compact">
          <div>
            <div class="section-label">到期预警</div>
            <h3 class="card-title">到期预警</h3>
          </div>
          <div class="pill-group">
            <span class="metric-pill warning">30 天内 {{ expirySummary.urgent }}</span>
            <span class="metric-pill info">90 天内 {{ alerts.length }}</span>
          </div>
        </div>

        <div class="alert-stack">
          <article v-for="item in alerts" :key="item.id" class="alert-panel" :class="{ urgent: item.days_left <= 30 }">
            <div>
              <strong>{{ item.tenant_name || item.contract_no }}</strong>
              <p>{{ item.contract_no }} · {{ item.room_no }} · 到期日 {{ item.end_date }}</p>
            </div>
            <div class="alert-days">
              <span>剩余天数</span>
              <strong>{{ item.days_left }}</strong>
            </div>
          </article>
          <div v-if="!alerts.length" class="compact-empty">90 天内暂无到期合同</div>
        </div>

        <div class="dashboard-inline-stats">
          <div v-for="item in alertHighlights" :key="item.label" class="mini-stat">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>
      </article>

      <article class="card dashboard-card">
        <div class="card-header compact">
          <div>
            <div class="section-label">状态分布</div>
            <h3 class="card-title">合同状态概览</h3>
          </div>
        </div>

        <div class="chart-layout">
          <EChartPanel :option="statusOption" :empty="!statusRows.length" empty-text="暂无合同状态分布数据" :height="240" />
          <div class="legend-list">
            <div v-for="item in statusRows" :key="item.label" class="legend-row">
              <div class="legend-meta">
                <div class="legend-title">
                  <span class="status-swatch" :style="{ background: item.color }" />
                  <span>{{ item.label }}</span>
                </div>
                <small>{{ item.percent }}%</small>
              </div>
              <strong>{{ item.count }}</strong>
            </div>
          </div>
        </div>
      </article>

      <article class="card dashboard-card wide">
        <div class="card-header compact">
          <div>
            <div class="section-label">楼栋表现</div>
            <h3 class="card-title">楼栋/区域表现</h3>
          </div>
        </div>

        <div class="tower-summary-grid">
          <article v-for="item in towerRows" :key="item.tower" class="tower-summary-card">
            <div class="tower-summary-head">
              <strong>{{ item.tower }}</strong>
              <span>{{ item.count }} 份执行中合同</span>
            </div>
            <div class="tower-summary-metrics">
              <div>
                <span>在租面积</span>
                <strong>{{ formatArea(item.area) }} ㎡</strong>
              </div>
              <div>
                <span>出租率</span>
                <strong>{{ item.occupancyRate }}%</strong>
              </div>
              <div>
                <span>平均日租</span>
                <strong>{{ formatCurrency(item.avgDailyRent) }} 元/㎡/天</strong>
              </div>
              <div>
                <span>月租金</span>
                <strong>{{ formatCurrency(item.rent) }}</strong>
              </div>
            </div>
            <div class="tower-summary-foot">
              <span>收入占比 {{ item.revenueShare }}%</span>
                <span>单份合同平均面积 {{ formatArea(item.avgContractArea) }} ㎡</span>
            </div>
          </article>
        </div>

        <div class="tower-chart-grid">
          <div class="tower-chart-panel">
            <div class="section-label">面积去化</div>
            <EChartPanel :option="towerMixOption" :empty="!towerRows.length" empty-text="暂无楼栋面积数据" :height="300" />
          </div>
          <div class="tower-chart-panel">
            <div class="section-label">租金表现</div>
            <EChartPanel :option="towerRentOption" :empty="!towerRows.length" empty-text="暂无楼栋租金数据" :height="300" />
          </div>
        </div>
      </article>

      <article class="card dashboard-card wide">
        <div class="card-header compact">
          <div>
            <div class="section-label">收入预测</div>
            <h3 class="card-title">未来 12 个月收入预测</h3>
          </div>
        </div>

        <EChartPanel :option="forecastOption" :empty="!forecastRows.length" empty-text="暂无未来收入预测" :height="320" />
      </article>
    </div>
  </section>
</template>
