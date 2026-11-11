<script setup>
const props = defineProps({
  cards: {
    type: Array,
    default: () => []
  },
  allowBusinessNavigation: {
    type: Boolean,
    default: true
  },
  allowEvidence: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['select-building', 'navigate'])

function selectBuilding(card) {
  if (card?.code && card?.presentationEnabled !== false) emit('select-building', card.code)
}

function openMetric(card, metric) {
  if (!props.allowBusinessNavigation || !card?.code || !metric?.target) return
  emit('navigate', { tab: metric.target, metric: metric.key, buildingCode: card.code })
}

function openEvidence(card, metric) {
  if (!props.allowEvidence || !card?.code || !metric?.key) return
  emit('navigate', {
    tab: 'evidence',
    buildingCode: card.code,
    metric: metric.key,
    asOfDate: card.asOfDate || ''
  })
}
</script>

<template>
  <section class="portfolio-buildings" aria-labelledby="portfolio-buildings-title">
    <header class="portfolio-section-header">
      <div>
        <span class="portfolio-section-index">01</span>
        <h2 id="portfolio-buildings-title">{{ $t("楼宇经营对照") }}</h2>
      </div>
      <p>{{ $t("点击楼名查看详情") }}</p>
    </header>

    <div v-if="cards.length" class="portfolio-building-grid">
      <article
        v-for="card in cards"
        :key="card.key"
        class="portfolio-building-card"
        :class="`is-${card.statusTone}`"
        :data-operating-state="card.operatingState || 'unknown'"
        :data-testid="`portfolio-building-${card.code}`"
      >
        <header>
          <button
            type="button"
            class="portfolio-building-name"
            :title="card.presentationEnabled === false ? '该楼宇当前不开放单楼概况' : card.name"
            :disabled="card.presentationEnabled === false"
            @click="selectBuilding(card)"
          >
            <strong>{{ card.name }}</strong>
            <small>{{ $t(card.code) }}</small>
          </button>
          <span
            class="portfolio-building-status"
            :data-testid="`portfolio-building-status-${card.code}`"
          >{{ $t(card.statusLabel) }}</span>
        </header>

        <div v-if="card.metrics.length" class="portfolio-building-metrics">
          <div v-for="metric in card.metrics" :key="metric.key" class="portfolio-building-metric-row">
            <button
              type="button"
              class="portfolio-building-metric"
              :class="{ unavailable: !metric.available }"
              :disabled="!allowBusinessNavigation || !metric.target"
              :title="$t(metric.unavailableReason || metric.label)"
              @click="openMetric(card, metric)"
            >
              <span>{{ $t(metric.label) }}</span>
              <b>{{ $t(metric.value) }}</b>
            </button>
            <button
              v-if="allowEvidence"
              type="button"
              class="portfolio-building-evidence"
              :aria-label="`查看${card.name}${metric.label}依据`"
              :data-testid="`portfolio-building-evidence-${card.code}-${metric.key}`"
              @click="openEvidence(card, metric)"
            >{{ $t(" 查看依据 ") }}</button>
          </div>
        </div>

        <footer>
          <span v-if="card.metricCount">{{ $t(card.availableCount) }}/{{ $t(card.metricCount) }}{{ $t(" 项可用") }}</span>
          <span v-else>{{ $t("不展示逐楼指标") }}</span>
          <span v-if="card.reason" :title="card.reason">{{ card.reason }}</span>
          <span v-else-if="card.publishedAt">{{ $t("发布 ") }}{{ $t(card.publishedAt.slice(0, 10)) }}</span>
          <span v-else>{{ $t("同日口径") }}</span>
        </footer>
      </article>
    </div>

    <div v-else class="portfolio-building-empty">{{ $t(" 暂无可展示的楼宇数据。 ") }}</div>
  </section>
</template>

<style scoped>
.portfolio-buildings {
  min-height: 0;
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 11px;
}

.portfolio-section-header {
  display: flex;
  flex: 0 0 auto;
  align-items: flex-end;
  justify-content: space-between;
  min-width: 0;
}

.portfolio-section-header > div {
  display: flex;
  align-items: center;
  gap: 10px;
}

.portfolio-section-index {
  color: #38bdf8;
  font-family: Consolas, monospace;
  font-size: 11px;
  letter-spacing: 1px;
}

.portfolio-section-header h2 {
  margin: 0;
  color: #dce8ff;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 2px;
}

.portfolio-section-header p {
  margin: 0;
  color: #607398;
  font-size: 11px;
}

.portfolio-building-grid {
  min-height: 0;
  display: grid;
  flex: 1 1 auto;
  grid-template-columns: repeat(auto-fit, minmax(205px, 1fr));
  gap: 12px;
}

.portfolio-building-card {
  position: relative;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  overflow: hidden;
  border: 1px solid rgba(90, 140, 255, 0.18);
  border-radius: 13px;
  background:
    linear-gradient(150deg, rgba(20, 38, 70, 0.94), rgba(10, 21, 41, 0.88)),
    rgba(10, 21, 41, 0.9);
}

.portfolio-building-card::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: #64748b;
  content: '';
}

.portfolio-building-card.is-live::before { background: #38bdf8; }
.portfolio-building-card.is-published::before { background: #34d399; }
.portfolio-building-card.is-reviewed::before { background: #fbbf24; }
.portfolio-building-card.is-missing::before { background: #f87171; }

.portfolio-building-card > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.portfolio-building-name {
  appearance: none;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.portfolio-building-name strong {
  max-width: 100%;
  overflow: hidden;
  color: #edf4ff;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.portfolio-building-name small {
  color: #607398;
  font-family: Consolas, monospace;
  font-size: 10px;
  letter-spacing: 1px;
}

.portfolio-building-name:focus-visible {
  border-radius: 4px;
  outline: 2px solid rgba(56, 189, 248, 0.55);
  outline-offset: 3px;
}

.portfolio-building-status {
  flex: 0 0 auto;
  padding: 3px 7px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 999px;
  color: #a7b5cd;
  font-size: 9px;
  white-space: nowrap;
}

.is-live .portfolio-building-status { border-color: rgba(56, 189, 248, 0.3); color: #7dd3fc; }
.is-published .portfolio-building-status { border-color: rgba(52, 211, 153, 0.3); color: #6ee7b7; }
.is-reviewed .portfolio-building-status { border-color: rgba(251, 191, 36, 0.3); color: #fcd34d; }
.is-missing .portfolio-building-status { border-color: rgba(248, 113, 113, 0.3); color: #fca5a5; }

.portfolio-building-metrics {
  min-height: 0;
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  justify-content: space-evenly;
}

.portfolio-building-metric-row {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid rgba(90, 140, 255, 0.09);
}

.portfolio-building-metric-row:last-child {
  border-bottom: 0;
}

.portfolio-building-metric {
  appearance: none;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.portfolio-building-metric span {
  color: #7e91b5;
  font-size: 10px;
}

.portfolio-building-metric b {
  color: #dce8ff;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.portfolio-building-metric:hover b,
.portfolio-building-metric:focus-visible b {
  color: #7dd3fc;
}

.portfolio-building-metric:focus-visible {
  outline: 0;
}

.portfolio-building-metric.unavailable b {
  color: #52627f;
}

.portfolio-building-metric:disabled {
  cursor: default;
}

.portfolio-building-evidence {
  appearance: none;
  padding: 3px 6px;
  border: 1px solid rgba(96, 165, 250, 0.18);
  border-radius: 5px;
  background: rgba(30, 64, 175, 0.08);
  color: #7183a5;
  font-size: 8px;
  line-height: 1.2;
  white-space: nowrap;
}

.portfolio-building-evidence:hover,
.portfolio-building-evidence:focus-visible {
  border-color: rgba(125, 211, 252, 0.42);
  color: #7dd3fc;
  outline: none;
}

.portfolio-building-card > footer {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(90, 140, 255, 0.1);
  color: #566886;
  font-size: 9px;
}

.portfolio-building-card > footer span:last-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.portfolio-building-empty {
  display: grid;
  flex: 1 1 auto;
  place-items: center;
  border: 1px dashed rgba(251, 191, 36, 0.26);
  border-radius: 13px;
  color: #c3a85b;
  font-size: 13px;
}

@media (max-width: 720px) {
  .portfolio-section-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
  }

  .portfolio-section-header p {
    max-width: 100%;
    line-height: 1.5;
  }

  .portfolio-building-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }

  .portfolio-building-card {
    padding: 13px 14px;
  }

  .portfolio-building-card > header,
  .portfolio-building-card > footer {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .portfolio-building-status,
  .portfolio-building-card > footer span:last-child {
    max-width: 100%;
    overflow: visible;
    line-height: 1.35;
    text-overflow: clip;
    white-space: normal;
  }
}
</style>
