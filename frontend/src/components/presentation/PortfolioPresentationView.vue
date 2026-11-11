<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  buildLeadershipOverviewBuildingCards,
  buildPortfolioBuildingCards,
  buildPortfolioHealth,
  buildPortfolioKpis,
  buildPortfolioQualityBuildingCards
} from '../../lib/presentationDomain'
import PortfolioBuildingGrid from './PortfolioBuildingGrid.vue'
import PresentationKpiStrip from './PresentationKpiStrip.vue'

const props = defineProps({
  snapshot: {
    type: Object,
    default: () => ({})
  },
  overviewOnly: {
    type: Boolean,
    default: false
  },
  embedded: {
    type: Boolean,
    default: false
  },
  availableTabs: {
    type: Array,
    default: null
  }
})

const emit = defineEmits(['select-building', 'navigate'])

const clock = ref('')
let clockTimer = 0

function updateClock() {
  clock.value = new Date().toLocaleString('zh-CN', { hour12: false })
}

const scopeTitle = computed(() => (
  props.snapshot?.scope?.display_name
  || props.snapshot?.scope?.scope_code
  || '示例资产组合'
))
function targetAllowed(target) {
  if (!Array.isArray(props.availableTabs)) return true
  return props.availableTabs.includes(target)
}

function restrictTargets(items) {
  return items.map((item) => ({
    ...item,
    target: item.target && targetAllowed(item.target) ? item.target : null
  }))
}

function restrictBuildingTargets(cards) {
  return cards.map((card) => ({
    ...card,
    metrics: restrictTargets(Array.isArray(card.metrics) ? card.metrics : [])
  }))
}

const navigationOptions = computed(() => ({ allowBusinessNavigation: !props.overviewOnly }))
const kpis = computed(() => restrictTargets(buildPortfolioKpis(props.snapshot, navigationOptions.value)))
const qualityBuildingCards = computed(() => (
  props.overviewOnly
    ? buildLeadershipOverviewBuildingCards(props.snapshot, navigationOptions.value)
    : restrictBuildingTargets(buildPortfolioQualityBuildingCards(props.snapshot, navigationOptions.value))
))
const buildingCards = computed(() => (
  props.overviewOnly
    ? qualityBuildingCards.value
    : restrictBuildingTargets(buildPortfolioBuildingCards(props.snapshot, navigationOptions.value))
))
const health = computed(() => buildPortfolioHealth(props.snapshot, qualityBuildingCards.value))
const trendRows = computed(() => (
  Array.isArray(props.snapshot?.presentation?.trend) ? props.snapshot.presentation.trend : []
))
const missingBuildings = computed(() => (
  qualityBuildingCards.value.filter((item) => item.publicationStatus === 'missing')
))
const kpiByKey = computed(() => Object.fromEntries(kpis.value.map((item) => [item.key, item])))
const qualityPercent = computed(() => (
  health.value.authorized > 0
    ? Math.round((health.value.sameDaySnapshots / health.value.authorized) * 100)
    : 0
))
const gateNotice = computed(() => {
  const notices = []
  if (health.value.observed > 0) notices.push(`${health.value.observed} 栋观察态，不计入合计`)
  else if (health.value.notIncluded > 0) notices.push(`${health.value.notIncluded} 栋不计入合计`)
  if (health.value.aggregateOnly > 0) notices.push(`${health.value.aggregateOnly} 栋只计合计，仅显示状态`)
  if (health.value.closed > 0) notices.push(`${health.value.closed} 栋暂不展示且未计入合计`)
  return notices.join('；')
})

const riskSignals = computed(() => [
  kpiByKey.value['arrears.balance'],
  kpiByKey.value['expiry.area_pressure_365d']
].filter(Boolean))

function openEvidence() {
  if (props.overviewOnly) return
  emit('navigate', {
    tab: 'evidence',
    metric: 'occupancy.area_rate',
    asOfDate: props.snapshot?.as_of_date || ''
  })
}

function displayTimestamp(value) {
  if (!value) return '暂无可靠业务时间'
  return String(value).slice(0, 16).replace('T', ' ')
}

onMounted(() => {
  updateClock()
  clockTimer = window.setInterval(updateClock, 1000)
})

onBeforeUnmount(() => {
  window.clearInterval(clockTimer)
})
</script>

<template>
  <div
    class="portfolio-cockpit"
    :class="{ 'is-embedded': embedded }"
    data-testid="presentation-cockpit"
    data-scope-type="portfolio"
  >
    <header class="portfolio-header">
      <div class="portfolio-brand">
        <span class="portfolio-brand-mark">楼宇资产组合 · PORTFOLIO</span>
        <div>
          <strong>{{ scopeTitle }}</strong>
          <small>写字楼经营总览</small>
        </div>
      </div>
      <div class="portfolio-header-status">
        <span
          v-if="health.simulated"
          class="portfolio-simulation-badge"
          data-testid="portfolio-simulated-data"
        >模拟数据</span>
        <span v-if="gateNotice" class="portfolio-gate-status">{{ gateNotice }}</span>
        <span class="portfolio-asof"><i></i>统计日 {{ health.asOfDate }}</span>
        <span class="portfolio-clock">{{ clock }}</span>
      </div>
    </header>

    <PresentationKpiStrip
      :cards="kpis"
      layout="portfolio"
      :interactive="!overviewOnly"
      @navigate="emit('navigate', $event)"
    />

    <PortfolioBuildingGrid
      :cards="buildingCards"
      :allow-business-navigation="!overviewOnly"
      :allow-evidence="!overviewOnly && targetAllowed('evidence')"
      @select-building="emit('select-building', $event)"
      @navigate="emit('navigate', $event)"
    />

    <section class="portfolio-bottom" aria-label="趋势、风险与数据质量">
      <article class="portfolio-bottom-card portfolio-trend-card">
        <header><span>02</span><h2>板块趋势</h2></header>
        <div class="portfolio-trend-state">
          <strong>{{ trendRows.length }}</strong>
          <div>
            <b>个可比期间</b>
            <span v-if="trendRows.length < 2">至少需要两期数据才能显示趋势</span>
            <span v-else>历期变化</span>
          </div>
        </div>
        <footer>最新来源观测：{{ displayTimestamp(health.sourceObservedAt) }}</footer>
      </article>

      <article class="portfolio-bottom-card portfolio-risk-card">
        <header><span>03</span><h2>风险信号</h2></header>
        <button
          v-for="signal in riskSignals"
          :key="signal.key"
          type="button"
          :disabled="overviewOnly || !signal.target"
          @click="!overviewOnly && emit('navigate', { tab: signal.target, metric: signal.key })"
        >
          <span>{{ signal.label }}</span>
          <b :class="signal.tone">{{ signal.value }}</b>
        </button>
        <footer>缺失或过期数据不按零处理</footer>
      </article>

      <article
        v-if="overviewOnly"
        class="portfolio-bottom-card portfolio-quality-card"
        data-testid="portfolio-data-quality-summary"
      >
        <header><span>04</span><h2>数据质量</h2></header>
        <div class="portfolio-quality-summary">
          <strong>{{ health.sameDaySnapshots }}/{{ health.authorized }}</strong>
          <span>栋同日有快照</span>
          <i><b :style="{ width: `${qualityPercent}%` }"></b></i>
        </div>
        <div class="portfolio-quality-meta">
          <span>{{ health.complete }} 栋五项核心指标齐备</span>
          <span v-if="missingBuildings.length" class="warn">
            缺失：{{ missingBuildings.map((item) => item.name).join('、') }}
          </span>
          <span v-else>当前授权范围无整栋缺失</span>
        </div>
        <footer>缺失、过期和异常状态均按服务端概况投影展示</footer>
      </article>

      <button
        v-else
        type="button"
        class="portfolio-bottom-card portfolio-quality-card portfolio-quality-action"
        data-testid="portfolio-data-quality-evidence"
        @click="openEvidence"
      >
        <header><span>04</span><h2>数据质量</h2></header>
        <div class="portfolio-quality-summary">
          <strong>{{ health.sameDaySnapshots }}/{{ health.authorized }}</strong>
          <span>栋同日有快照</span>
          <i><b :style="{ width: `${qualityPercent}%` }"></b></i>
        </div>
        <div class="portfolio-quality-meta">
          <span>{{ health.complete }} 栋五项核心指标齐备</span>
          <span v-if="missingBuildings.length" class="warn">
            缺失：{{ missingBuildings.map((item) => item.name).join('、') }}
          </span>
          <span v-else>当前授权范围无整栋缺失</span>
        </div>
        <footer>点击查看计算方式与数据来源</footer>
      </button>
    </section>

    <footer class="portfolio-footer">
      <span>
        {{ health.simulated ? '当前均为模拟数据 · ' : '' }}所有合计均按原始分子、分母重算
      </span>
      <span>{{ overviewOnly ? '点击楼名查看同一安全投影内的单楼概况' : '点击楼名切换单楼大屏 · 点击指标进入对应业务页面' }}</span>
      <span v-if="!embedded" class="portfolio-exit">ESC 退出演示</span>
    </footer>
  </div>
</template>

<style scoped>
.portfolio-cockpit {
  position: fixed;
  inset: 0;
  z-index: 200;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px 28px 12px;
  overflow: auto;
  background:
    linear-gradient(rgba(82, 129, 196, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(82, 129, 196, 0.035) 1px, transparent 1px),
    radial-gradient(900px 480px at 14% -8%, rgba(20, 184, 166, 0.13), transparent 62%),
    radial-gradient(1100px 560px at 92% 0%, rgba(37, 99, 235, 0.17), transparent 60%),
    linear-gradient(155deg, #050b16 0%, #091426 55%, #07101f 100%);
  background-size: 28px 28px, 28px 28px, auto, auto, auto;
  color: #e8eefc;
}

.portfolio-cockpit.is-embedded {
  position: relative;
  inset: auto;
  z-index: auto;
  min-height: calc(100vh - 92px);
}

.portfolio-header {
  min-width: 0;
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.portfolio-brand {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 15px;
}

.portfolio-brand-mark {
  padding: 6px 9px;
  border: 1px solid rgba(56, 189, 248, 0.32);
  border-radius: 5px;
  color: #7dd3fc;
  font-family: Consolas, monospace;
  font-size: 9px;
  letter-spacing: 1.2px;
  white-space: nowrap;
}

.portfolio-brand > div {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.portfolio-brand strong {
  overflow: hidden;
  color: #f3f7ff;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: 4px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.portfolio-brand small {
  color: #607398;
  font-size: 10px;
  letter-spacing: 1px;
}

.portfolio-header-status {
  display: flex;
  align-items: center;
  gap: 16px;
  color: #8ea2c9;
  font-size: 11px;
  white-space: nowrap;
}

.portfolio-simulation-badge,
.portfolio-gate-status {
  padding: 5px 9px;
  border-radius: 4px;
  font-size: 9px;
  letter-spacing: 0.8px;
}

.portfolio-simulation-badge {
  border: 1px solid rgba(251, 191, 36, 0.42);
  background: rgba(120, 72, 8, 0.18);
  color: #fcd34d;
  font-weight: 700;
  letter-spacing: 1.4px;
}

.portfolio-gate-status {
  border: 1px solid rgba(125, 211, 252, 0.22);
  background: rgba(14, 116, 144, 0.09);
  color: #93c5fd;
}

.portfolio-asof {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 9px;
  border: 1px solid rgba(52, 211, 153, 0.2);
  border-radius: 999px;
  color: #6ee7b7;
}

.portfolio-asof i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #34d399;
  box-shadow: 0 0 9px rgba(52, 211, 153, 0.8);
}

.portfolio-clock {
  font-family: Consolas, monospace;
  font-variant-numeric: tabular-nums;
}

.portfolio-bottom {
  min-height: 112px;
  display: grid;
  flex: 0 0 140px;
  grid-template-columns: 1.1fr 0.9fr 1.2fr;
  gap: 12px;
}

.portfolio-bottom-card {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 11px 14px;
  overflow: hidden;
  border: 1px solid rgba(90, 140, 255, 0.16);
  border-radius: 12px;
  background: rgba(12, 25, 47, 0.8);
  color: inherit;
  font: inherit;
  text-align: left;
}

.portfolio-quality-action {
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease, transform 0.18s ease;
}

.portfolio-quality-action:hover,
.portfolio-quality-action:focus-visible {
  border-color: rgba(125, 211, 252, 0.42);
  background: rgba(18, 36, 65, 0.92);
  outline: none;
  transform: translateY(-1px);
}

.portfolio-bottom-card > header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.portfolio-bottom-card > header span {
  color: #38bdf8;
  font-family: Consolas, monospace;
  font-size: 9px;
}

.portfolio-bottom-card h2 {
  margin: 0;
  color: #bcd0f5;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 1.5px;
}

.portfolio-bottom-card > footer {
  margin-top: auto;
  color: #566886;
  font-size: 9px;
}

.portfolio-trend-state {
  display: flex;
  align-items: center;
  gap: 12px;
}

.portfolio-trend-state > strong {
  color: #7dd3fc;
  font-family: Consolas, monospace;
  font-size: 34px;
  line-height: 1;
}

.portfolio-trend-state > div {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.portfolio-trend-state b {
  color: #dce8ff;
  font-size: 11px;
}

.portfolio-trend-state span {
  overflow: hidden;
  color: #7183a5;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.portfolio-risk-card button {
  appearance: none;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 3px 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.portfolio-risk-card button span {
  color: #7183a5;
  font-size: 10px;
}

.portfolio-risk-card button b {
  font-size: 15px;
  font-variant-numeric: tabular-nums;
}

.portfolio-risk-card button b.red { color: #fca5a5; }
.portfolio-risk-card button b.amber { color: #fcd34d; }

.portfolio-quality-summary {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: baseline;
  gap: 3px 8px;
}

.portfolio-quality-summary strong {
  color: #6ee7b7;
  font-family: Consolas, monospace;
  font-size: 24px;
}

.portfolio-quality-summary > span {
  color: #8ea2c9;
  font-size: 10px;
}

.portfolio-quality-summary i {
  grid-column: 1 / -1;
  height: 5px;
  overflow: hidden;
  border-radius: 4px;
  background: rgba(90, 140, 255, 0.1);
}

.portfolio-quality-summary i b {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #38bdf8, #34d399);
}

.portfolio-quality-meta {
  min-width: 0;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: #7183a5;
  font-size: 9px;
}

.portfolio-quality-meta span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.portfolio-quality-meta .warn {
  color: #fcd34d;
}

.portfolio-footer {
  min-width: 0;
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 18px;
  color: #566886;
  font-size: 9px;
  white-space: nowrap;
}

.portfolio-exit {
  margin-left: auto;
}

@media (max-width: 1400px) {
  .portfolio-cockpit {
    gap: 9px;
    padding: 14px 18px 8px;
  }

  .portfolio-brand strong {
    font-size: 18px;
  }

  .portfolio-bottom {
    flex-basis: 125px;
    min-height: 118px;
  }

  .portfolio-footer span:nth-child(2) {
    display: none;
  }
}

@media (max-width: 720px) {
  .portfolio-cockpit,
  .portfolio-cockpit.is-embedded {
    gap: 12px;
    padding: 14px 18px 16px;
  }

  .portfolio-header {
    align-items: stretch;
    flex-direction: column;
    gap: 10px;
  }

  .portfolio-brand {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-items: center;
    gap: 7px 10px;
  }

  .portfolio-brand-mark,
  .portfolio-brand > div {
    grid-column: 1 / -1;
  }

  .portfolio-brand strong {
    overflow: visible;
    font-size: 18px;
    letter-spacing: 1.5px;
    line-height: 1.35;
    text-overflow: clip;
    white-space: normal;
  }

  .portfolio-brand small {
    display: block;
    line-height: 1.45;
  }

  .portfolio-header-status {
    align-items: center;
    flex-wrap: wrap;
    gap: 7px;
    line-height: 1.45;
    white-space: normal;
  }

  .portfolio-gate-status {
    flex: 1 1 100%;
  }

  .portfolio-clock {
    margin-left: auto;
  }

  .portfolio-bottom {
    min-height: 0;
    flex: 0 0 auto;
    grid-template-columns: minmax(0, 1fr);
  }

  .portfolio-bottom-card {
    min-height: 126px;
  }

  .portfolio-trend-state span,
  .portfolio-quality-meta span {
    overflow: visible;
    line-height: 1.4;
    text-overflow: clip;
    white-space: normal;
  }

  .portfolio-quality-meta,
  .portfolio-footer {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
    line-height: 1.45;
    white-space: normal;
  }
}

@media (max-width: 420px) {
  .portfolio-cockpit,
  .portfolio-cockpit.is-embedded {
    padding-inline: 14px;
  }

  .portfolio-clock {
    display: none;
  }
}
</style>
