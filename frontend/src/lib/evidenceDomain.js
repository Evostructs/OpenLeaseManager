export const DEFAULT_EVIDENCE_METRIC = 'occupancy.area_rate'

const ITEM_STATUS = Object.freeze({
  included: { label: '已纳入合计', tone: 'included' },
  excluded: { label: '有数据未纳入', tone: 'excluded' },
  missing: { label: '缺少同日数据', tone: 'missing' },
  live_detail_limited: { label: '实时明细受限', tone: 'limited' }
})

const STATUS_COPY = Object.freeze({
  current: '当前',
  stale: '过期',
  unknown: '未知',
  complete: '完整',
  partial: '部分',
  missing: '缺失',
  valid: '有效',
  rejected: '已拒绝',
  pending: '待校验',
  draft: '草稿',
  reviewed: '已复核',
  published: '已发布',
  superseded: '已替代',
  live_adapter: '实时适配',
  accepted: '已接收',
  received: '已接收',
  validating: '校验中',
  active: '启用',
  inactive: '停用',
  detail: '明细',
  snapshot: '快照',
  mixed: '混合来源',
  master_data: '主数据',
  manual_snapshot: '人工快照',
  excel: 'Excel'
})

export function evidenceNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function formatNumber(value, maximumFractionDigits = 2, minimumFractionDigits = 0) {
  const number = evidenceNumber(value)
  if (number === null) return '--'
  return number.toLocaleString('zh-CN', { minimumFractionDigits, maximumFractionDigits })
}

export function formatEvidenceScalar(value) {
  return formatNumber(value, 6)
}

export function formatEvidenceValue(value, unit = '') {
  const number = evidenceNumber(value)
  if (number === null) return '--'
  const normalized = String(unit || '').trim().toLowerCase()
  if (['percent', 'percentage', '%'].includes(normalized)) return `${formatNumber(number, 2)}%`
  if (['cny', 'rmb', 'yuan', '元'].includes(normalized)) return `${formatNumber(number, 2, 2)} 元`
  if (['sqm', 'm2', '㎡', 'square_meter'].includes(normalized)) return `${formatNumber(number, 2)} ㎡`
  if (['cny/sqm/day', 'yuan/sqm/day', '元/㎡·天'].includes(normalized)) return `${formatNumber(number, 4)} 元/㎡·天`
  if (['building', 'buildings', '栋'].includes(normalized)) return `${formatNumber(number, 0)} 栋`
  if (['contract', 'contracts', '份'].includes(normalized)) return `${formatNumber(number, 0)} 份`
  if (['item', 'items', '项'].includes(normalized)) return `${formatNumber(number, 0)} 项`
  return unit ? `${formatNumber(number, 6)} ${unit}` : formatNumber(number, 6)
}

export function formatEvidenceDateTime(value) {
  if (!value) return '--'
  return String(value).replace('T', ' ').slice(0, 19)
}

export function compactEvidenceReference(value) {
  const text = String(value ?? '').trim()
  if (!text) return '--'
  if (text.length <= 22) return text
  return `${text.slice(0, 10)}…${text.slice(-8)}`
}

export function evidenceStatusMeta(status) {
  return ITEM_STATUS[status] || { label: status || '状态未知', tone: 'limited' }
}

export function evidenceStatusLabel(status) {
  const normalized = String(status || '').trim()
  return STATUS_COPY[normalized] || normalized || '--'
}

export function buildEvidenceCoverage(coverage = {}) {
  const authorized = evidenceNumber(coverage?.authorized_buildings)
  const eligible = evidenceNumber(coverage?.eligible_buildings)
  const included = evidenceNumber(coverage?.included_buildings)
  const coveredArea = evidenceNumber(coverage?.covered_area_sqm)
  const knownArea = evidenceNumber(coverage?.known_total_area_sqm ?? coverage?.eligible_area_sqm)
  const coveredDays = evidenceNumber(coverage?.covered_days)
  const eligibleDays = evidenceNumber(coverage?.eligible_days)
  return [
    {
      key: 'buildings',
      label: '楼宇覆盖',
      value: included === null ? '--' : `${formatNumber(included, 0)} / ${authorized === null ? '--' : formatNumber(authorized, 0)}`,
      hint: eligible === null ? '具备数据楼宇待确认' : `${formatNumber(eligible, 0)} 栋具备数据`
    },
    {
      key: 'area',
      label: '面积覆盖',
      value: coveredArea === null ? '--' : `${formatNumber(coveredArea, 0)} ㎡`,
      hint: knownArea === null ? '可靠总面积待确认' : `可靠总面积 ${formatNumber(knownArea, 0)} ㎡`
    },
    {
      key: 'days',
      label: '时间覆盖',
      value: coveredDays === null ? '--' : `${formatNumber(coveredDays, 0)} 天`,
      hint: eligibleDays === null ? '应覆盖天数待确认' : `应覆盖 ${formatNumber(eligibleDays, 0)} 天`
    },
    {
      key: 'estimate',
      label: '估算占比',
      value: evidenceNumber(coverage?.estimate_share) === null
        ? '--'
        : `${formatNumber(Number(coverage.estimate_share) * 100, 1)}%`,
      hint: '估算不替代来源事实'
    }
  ]
}

export function buildEvidenceItems(payload = {}) {
  const metricUnit = payload?.metric?.unit || ''
  const items = Array.isArray(payload?.items) ? payload.items : []
  const isSingleBuildingScope = payload?.scope?.scope_type === 'building'
  return items.map((item) => {
    const fact = item?.fact || null
    const source = item?.source || null
    const batch = item?.batch || null
    const record = item?.record || null
    const snapshot = item?.snapshot || null
    const status = isSingleBuildingScope && item?.status === 'included'
      ? { label: '单楼指标可用', tone: 'included' }
      : evidenceStatusMeta(item?.status)
    const limitations = (Array.isArray(item?.limitations) ? item.limitations : [])
      .filter((value, index, rows) => value && value !== item?.reason && rows.indexOf(value) === index)
    return {
      key: item?.building?.code || item?.building?.id || snapshot?.id || items.indexOf(item),
      building: {
        id: item?.building?.id ?? null,
        code: item?.building?.code || '',
        name: item?.building?.name || item?.building?.code || '未命名楼宇'
      },
      included: item?.included === true,
      status,
      reason: item?.reason || '',
      value: formatEvidenceValue(fact?.value, fact?.unit || metricUnit),
      numerator: formatEvidenceScalar(fact?.numerator),
      denominator: formatEvidenceScalar(fact?.denominator),
      fact,
      source,
      batch,
      record,
      snapshot,
      operatingGate: item?.operating_gate || null,
      dataClassification: item?.data_classification || payload?.data_classification || '',
      limitations,
      hasEvidenceChain: Boolean(source || batch || record || snapshot)
    }
  })
}
