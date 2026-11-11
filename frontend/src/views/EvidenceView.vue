<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../lib/api'
import {
  buildEvidenceCoverage,
  buildEvidenceItems,
  compactEvidenceReference,
  DEFAULT_EVIDENCE_METRIC,
  evidenceNumber,
  evidenceStatusLabel,
  formatEvidenceDateTime,
  formatEvidenceScalar,
  formatEvidenceValue
} from '../lib/evidenceDomain'

const props = defineProps({
  user: {
    type: Object,
    default: () => ({ role: 'viewer' })
  },
  metricCode: {
    type: String,
    default: DEFAULT_EVIDENCE_METRIC
  },
  asOfDate: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['notify', 'change-query'])

const evidence = ref({})
const loading = ref(false)
const error = ref('')
let requestRevision = 0

const metric = computed(() => evidence.value?.metric || {})
const aggregate = computed(() => metric.value?.aggregate || {})
const coverageCards = computed(() => buildEvidenceCoverage(aggregate.value?.coverage || {}))
const buildingItems = computed(() => buildEvidenceItems(evidence.value))
const limitations = computed(() => (
  Array.isArray(evidence.value?.limitations) ? evidence.value.limitations.filter(Boolean) : []
))
const isSimulated = computed(() => evidence.value?.data_classification === 'simulated')
const scopeLabel = computed(() => (
  evidence.value?.scope?.display_name
  || evidence.value?.scope?.building?.name
  || evidence.value?.scope?.scope_code
  || '当前授权范围'
))
const scopeTypeLabel = computed(() => (
  evidence.value?.scope?.scope_type === 'portfolio' ? '板块底稿' : '楼宇底稿'
))
const selectedMetricCode = computed(() => props.metricCode || DEFAULT_EVIDENCE_METRIC)
const selectedDate = computed(() => props.asOfDate || evidence.value?.as_of_date || '')
const metricOptions = computed(() => {
  const rows = Array.isArray(evidence.value?.available_metrics) ? evidence.value.available_metrics : []
  if (rows.some((item) => item?.code === selectedMetricCode.value)) return rows
  return [
    { code: selectedMetricCode.value, name: metric.value?.name || selectedMetricCode.value, unit: metric.value?.unit || '' },
    ...rows
  ]
})
const ruleRows = computed(() => [
  { key: 'numerator', label: '分子规则', value: metric.value?.numerator_rule },
  { key: 'denominator', label: '分母规则', value: metric.value?.denominator_rule },
  { key: 'inclusion', label: '纳入规则', value: metric.value?.inclusion_rule },
  { key: 'exclusion', label: '排除规则', value: metric.value?.exclusion_rule },
  { key: 'aggregation', label: '汇总规则', value: metric.value?.aggregation_rule },
  { key: 'coverage', label: '覆盖规则', value: metric.value?.coverage_rule },
  { key: 'estimation', label: '估算政策', value: metric.value?.estimation_policy },
  { key: 'freshness', label: '新鲜度政策', value: metric.value?.freshness_policy }
].filter((item) => item.value))

function gateLabel(gate) {
  if (!gate) return ''
  if (gate.state === 'open') return '展示并计入合计'
  if (gate.state === 'observe') return '观察态 · 不计入合计'
  if (gate.state === 'aggregate_only') return '不展示 · 计入合计'
  return '暂不展示 · 不计入合计'
}

function buildRequestPath() {
  const params = new URLSearchParams()
  params.set('metric_code', selectedMetricCode.value)
  if (props.asOfDate) params.set('as_of_date', props.asOfDate)
  return `/api/operations/snapshot-evidence?${params.toString()}`
}

async function loadEvidence({ announce = false } = {}) {
  const revision = ++requestRevision
  loading.value = true
  error.value = ''
  try {
    const payload = await api(buildRequestPath())
    if (revision !== requestRevision) return
    evidence.value = payload || {}
    const resolvedMetric = payload?.metric?.code || selectedMetricCode.value
    const resolvedDate = payload?.as_of_date || props.asOfDate
    if (resolvedMetric !== props.metricCode || resolvedDate !== props.asOfDate) {
      emit('change-query', { metricCode: resolvedMetric, asOfDate: resolvedDate })
    }
    if (announce) emit('notify', { text: '数据依据已刷新' })
  } catch (requestError) {
    if (revision !== requestRevision) return
    evidence.value = {}
    error.value = requestError.message || '数据依据加载失败'
  } finally {
    if (revision === requestRevision) loading.value = false
  }
}

function changeMetric(event) {
  const metricCode = String(event.target.value || '').trim() || DEFAULT_EVIDENCE_METRIC
  emit('change-query', { metricCode, asOfDate: selectedDate.value })
}

function changeDate(event) {
  const asOfDate = String(event.target.value || '').trim()
  if (!asOfDate) return
  emit('change-query', { metricCode: selectedMetricCode.value, asOfDate })
}

watch(
  () => [props.metricCode, props.asOfDate],
  () => loadEvidence(),
  { immediate: true }
)
</script>

<template>
  <section class="content evidence-page" data-testid="evidence-page">
    <header class="evidence-masthead">
      <div class="evidence-masthead-copy">
        <span class="evidence-kicker">AUDIT WORKPAPER · {{ $t(evidence.definition_version || 'OPS_V1') }}</span>
        <span v-if="isSimulated" class="badge warning" data-testid="evidence-simulated-badge">{{ $t("模拟数据") }}</span>
        <h2>{{ $t("经营指标审计底稿") }}</h2>
        <p>{{ $t("查看指标计算方式和各楼宇数据来源。") }}</p>
      </div>
      <div class="evidence-stamp" :aria-label="$t(&quot;底稿范围&quot;)">
        <span>{{ $t(scopeTypeLabel) }}</span>
        <strong>{{ $t(scopeLabel) }}</strong>
        <small>{{ $t("统计日 ") }}{{ $t(evidence.as_of_date || selectedDate || '--') }}</small>
      </div>
    </header>

    <div class="evidence-toolbar card" data-testid="evidence-toolbar">
      <label>
        <span>{{ $t("核对指标") }}</span>
        <select class="select" :value="selectedMetricCode" data-testid="evidence-metric-select" @change="changeMetric">
          <option v-for="item in metricOptions" :key="item.code" :value="item.code">
            {{ $t(item.name || item.code) }}{{ $t(item.unit ? ` · ${item.unit}` : '') }}{{ $t(item.scope_status === 'not_provided' ? ' · 当前明细未提供' : '') }}
          </option>
        </select>
      </label>
      <label>
        <span>{{ $t("目标统计日") }}</span>
        <input class="input evidence-date-input" type="date" :value="selectedDate" data-testid="evidence-date-input" @change="changeDate" />
      </label>
      <div class="evidence-toolbar-meta">
        <span>{{ $t("契约") }}</span>
        <strong>{{ $t(evidence.contract_version || '--') }}</strong>
      </div>
      <button class="btn" type="button" :disabled="loading" data-testid="evidence-refresh" @click="loadEvidence({ announce: true })">
        {{ $t(loading ? '核对中…' : '重新核对') }}
      </button>
    </div>

    <article v-if="error" class="evidence-error card" data-testid="evidence-error">
      <div>
        <span>{{ $t("底稿暂不可用") }}</span>
        <strong>{{ $t(error) }}</strong>
        <small>{{ $t("当前范围和未授权对象使用同一错误提示，不显示对象是否存在。") }}</small>
      </div>
      <button class="btn" type="button" @click="loadEvidence()">{{ $t("重试") }}</button>
    </article>

    <div v-else-if="loading && !metric.code" class="evidence-loading" data-testid="evidence-loading" aria-hidden="true">
      <span v-for="index in 6" :key="index"></span>
    </div>

    <template v-else>
      <article class="evidence-summary-sheet card" data-testid="evidence-summary">
        <div class="evidence-sheet-heading">
          <div>
            <span class="evidence-sheet-index">WP-01</span>
            <h3>{{ $t(metric.name || selectedMetricCode) }}</h3>
            <p>{{ $t(metric.formula || '暂无计算公式') }}</p>
          </div>
          <div class="evidence-definition-ref">
            <span>{{ $t("口径版本 ") }}{{ $t(metric.definition_version || evidence.definition_version || '--') }}</span>
            <code :title="$t(metric.definition_hash || '')">{{ $t(compactEvidenceReference(metric.definition_hash)) }}</code>
          </div>
        </div>

        <div class="evidence-ledger-values">
          <section class="evidence-ledger-primary" :class="{ unavailable: evidenceNumber(aggregate.value) === null }">
            <span>{{ $t("已发布合计") }}</span>
            <strong>{{ $t(formatEvidenceValue(aggregate.value, metric.unit)) }}</strong>
            <small v-if="aggregate.unavailable_reason">{{ $t(aggregate.unavailable_reason) }}</small>
            <small v-else>{{ $t("计算版本 ") }}{{ $t(aggregate.calculation_version || '--') }}</small>
          </section>
          <section>
            <span>{{ $t("原始分子") }}</span>
            <strong>{{ $t(formatEvidenceScalar(aggregate.numerator)) }}</strong>
            <small>{{ $t(metric.numerator_rule || '按已发布事实汇总') }}</small>
          </section>
          <section>
            <span>{{ $t("原始分母") }}</span>
            <strong>{{ $t(formatEvidenceScalar(aggregate.denominator)) }}</strong>
            <small>{{ $t(metric.denominator_rule || '合计型指标不适用分母') }}</small>
          </section>
          <section>
            <span>{{ $t("统计期间") }}</span>
            <strong class="evidence-period">{{ $t(aggregate.period_start || '--') }}<i></i>{{ $t(aggregate.period_end || '--') }}</strong>
            <small>{{ $t("统计截止 ") }}{{ $t(aggregate.as_of_date || evidence.as_of_date || '--') }}</small>
          </section>
        </div>

        <div class="evidence-coverage-grid" :aria-label="$t(&quot;覆盖情况&quot;)">
          <section v-for="item in coverageCards" :key="item.key">
            <span>{{ $t(item.label) }}</span>
            <strong>{{ $t(item.value) }}</strong>
            <small>{{ $t(item.hint) }}</small>
          </section>
        </div>

        <details class="evidence-rulebook">
          <summary>
            <span>{{ $t("口径规则册") }}</span>
            <strong>{{ $t(metric.owner ? `责任人 ${metric.owner}` : '查看指标定义与统计范围') }}</strong>
          </summary>
          <div class="evidence-rule-grid">
            <section v-for="rule in ruleRows" :key="rule.key">
              <span>{{ $t(rule.label) }}</span>
              <p>{{ $t(rule.value) }}</p>
            </section>
          </div>
        </details>
      </article>

      <article v-if="limitations.length" class="evidence-limitations card" data-testid="evidence-limitations">
        <div>
          <span>{{ $t("底稿限制") }}</span>
          <strong>{{ $t("以下事项不影响证据留存，但限制当前结论") }}</strong>
        </div>
        <ul>
          <li v-for="item in limitations" :key="item">{{ $t(item) }}</li>
        </ul>
      </article>

      <section class="evidence-building-section" aria-labelledby="evidence-building-title">
        <header>
          <div>
            <span class="evidence-sheet-index">WP-02</span>
            <h3 id="evidence-building-title">{{ $t("逐楼证据链") }}</h3>
          </div>
          <p>{{ $t(buildingItems.length) }}{{ $t(" 栋位于当前授权与统计日范围") }}</p>
        </header>

        <article
          v-for="(item, index) in buildingItems"
          :key="item.key"
          class="evidence-building-sheet card"
          :class="`is-${item.status.tone}`"
          :data-testid="`evidence-building-${item.building.code}`"
        >
          <header class="evidence-building-heading">
            <span class="evidence-building-sequence">{{ $t(String(index + 1).padStart(2, '0')) }}</span>
            <div>
              <strong>{{ item.building.name }}</strong>
              <code>{{ $t(item.building.code || '--') }}</code>
              <span v-if="item.operatingGate" class="badge warning">{{ $t(gateLabel(item.operatingGate)) }}</span>
            </div>
            <span class="evidence-status" :class="`is-${item.status.tone}`">{{ $t(item.status.label) }}</span>
          </header>

          <div v-if="item.fact" class="evidence-fact-strip">
            <section>
              <span>{{ $t("楼宇贡献值") }}</span>
              <strong>{{ $t(item.value) }}</strong>
            </section>
            <section>
              <span>{{ $t("分子") }}</span>
              <strong>{{ $t(item.numerator) }}</strong>
            </section>
            <section>
              <span>{{ $t("分母") }}</span>
              <strong>{{ $t(item.denominator) }}</strong>
            </section>
            <section class="evidence-fact-statuses">
              <span>{{ $t(evidenceStatusLabel(item.fact.data_depth)) }}</span>
              <span>{{ $t(evidenceStatusLabel(item.fact.completeness_status)) }}</span>
              <span>{{ $t(evidenceStatusLabel(item.fact.freshness_status)) }}</span>
              <span>{{ $t(evidenceStatusLabel(item.fact.publication_status)) }}</span>
            </section>
          </div>

          <p v-if="item.reason" class="evidence-building-reason">{{ item.reason }}</p>

          <div v-if="item.hasEvidenceChain" class="evidence-chain" :aria-label="$t(&quot;来源证据链&quot;)">
            <section :class="{ empty: !item.source }">
              <span>{{ $t("01 · 来源") }}</span>
              <strong>{{ item.source?.name || '--' }}</strong>
              <small>{{ $t(evidenceStatusLabel(item.source?.source_type)) }} · {{ $t(item.source?.cadence || '更新频率未知') }}</small>
              <em>{{ $t(item.source?.owner ? `负责人 ${item.source.owner}` : '负责人待确认') }}</em>
            </section>
            <section :class="{ empty: !item.batch }">
              <span>{{ $t("02 · 批次") }}</span>
              <strong>{{ $t(item.batch?.external_batch_no || (item.batch?.id ? `#${item.batch.id}` : '--')) }}</strong>
              <small :title="$t(item.batch?.source_file_name || '')">{{ $t(item.batch?.source_file_name || '无源文件名') }}</small>
              <em>{{ item.batch ? `${evidenceStatusLabel(item.batch.status)} · ${item.batch.accepted_count ?? 0}/${item.batch.row_count ?? 0} 行接收 · 提供 ${item.batch.actor?.name || '待确认'}` : '批次不适用' }}</em>
            </section>
            <section :class="{ empty: !item.record }">
              <span>{{ $t("03 · 来源记录") }}</span>
              <strong>{{ $t(item.record?.external_record_id || (item.record?.row_no ? `第 ${item.record.row_no} 行` : '--')) }}</strong>
              <small :title="$t(item.record?.record_checksum || '')">{{ $t("校验 ") }}{{ $t(compactEvidenceReference(item.record?.record_checksum)) }}</small>
              <em>{{ $t(item.record ? evidenceStatusLabel(item.record.status) : '来源记录不适用') }}</em>
            </section>
            <section :class="{ empty: !item.snapshot }">
              <span>{{ $t("04 · 发布快照") }}</span>
              <strong>{{ $t(item.snapshot?.id ? `#${item.snapshot.id} · R${item.snapshot.revision}` : '--') }}</strong>
              <small>{{ $t(item.snapshot ? `${evidenceStatusLabel(item.snapshot.publication_status)} · ${item.snapshot.is_current ? '当前权威版' : '历史版'}` : '快照不适用') }}</small>
              <em>{{ $t("复核 ") }}{{ item.snapshot?.reviewed_by?.name || '待确认' }}{{ $t(" · 发布 ") }}{{ item.snapshot?.published_by?.name || '待确认' }} {{ $t(formatEvidenceDateTime(item.snapshot?.published_at)) }}</em>
            </section>
          </div>

          <details v-if="item.source || item.batch || item.record || item.snapshot || item.fact" class="evidence-technical-details">
            <summary>{{ $t("查看批次校验与版本细节") }}</summary>
            <div class="evidence-technical-grid">
              <dl>
                <div><dt>{{ $t("来源规则") }}</dt><dd>{{ $t(item.source?.freshness_rule || '--') }}</dd></div>
                <div><dt>{{ $t("来源状态") }}</dt><dd>{{ $t(item.source ? (item.source.is_active ? '启用' : '停用') : '--') }}</dd></div>
                <div><dt>{{ $t("来源观测") }}</dt><dd>{{ $t(formatEvidenceDateTime(item.batch?.source_observed_at)) }}</dd></div>
                <div><dt>{{ $t("来源更新") }}</dt><dd>{{ $t(formatEvidenceDateTime(item.batch?.source_updated_at)) }}</dd></div>
              </dl>
              <dl>
                <div><dt>{{ $t("解析器版本") }}</dt><dd>{{ $t(item.batch?.parser_version || '--') }}</dd></div>
                <div><dt>{{ $t("文件校验") }}</dt><dd :title="$t(item.batch?.file_checksum || '')">{{ $t(compactEvidenceReference(item.batch?.file_checksum)) }}</dd></div>
                <div><dt>{{ $t("接收 / 入库") }}</dt><dd>{{ $t(formatEvidenceDateTime(item.batch?.received_at)) }} / {{ $t(formatEvidenceDateTime(item.batch?.ingested_at)) }}</dd></div>
                <div><dt>{{ $t("更正原因") }}</dt><dd>{{ $t(item.batch?.correction_reason || '--') }}</dd></div>
              </dl>
              <dl>
                <div><dt>{{ $t("计算版本") }}</dt><dd>{{ $t(item.fact?.calculation_version || '--') }}</dd></div>
                <div><dt>{{ $t("展示 / 合计状态") }}</dt><dd>{{ $t(gateLabel(item.operatingGate) || '--') }}</dd></div>
                <div><dt>{{ $t("状态原因") }}</dt><dd>{{ item.operatingGate?.reason || '--' }}</dd></div>
                <div><dt>{{ $t("估算占比") }}</dt><dd>{{ $t(evidenceNumber(item.fact?.estimate_share) === null ? '--' : `${(Number(item.fact.estimate_share) * 100).toFixed(1)}%`) }}</dd></div>
                <div><dt>{{ $t("估算方法") }}</dt><dd>{{ $t(item.fact?.estimate_method || '--') }}</dd></div>
                <div><dt>{{ $t("来源定位") }}</dt><dd>{{ $t(item.record?.source_ref || '--') }}</dd></div>
              </dl>
            </div>
          </details>

          <ul v-if="item.limitations.length" class="evidence-item-limitations">
            <li v-for="limitation in item.limitations" :key="limitation">{{ $t(limitation) }}</li>
          </ul>
        </article>

        <div v-if="!buildingItems.length" class="evidence-empty card" data-testid="evidence-empty">{{ $t(" 暂无可查看的楼宇数据。 ") }}</div>
      </section>
    </template>
  </section>
</template>
