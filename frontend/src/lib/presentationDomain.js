const METRIC_PAGE = Object.freeze({
  'area.rentable_sqm': 'rooms',
  'area.leased_sqm': 'rooms',
  'occupancy.area_rate': 'rooms',
  'rent.effective_daily': 'analytics',
  'billing.receivable_month': 'receivables',
  'collection.received_month': 'receivables',
  'collection.rate_month': 'receivables',
  'arrears.balance': 'receivables',
  'expiry.area_pressure_365d': 'contracts',
  'expiry.rent_12m': 'contracts',
  'risk.open_items': 'risks'
})

const STATUS_COPY = Object.freeze({
  live_adapter: { label: '实时明细', tone: 'live' },
  published: { label: '已复核发布', tone: 'published' },
  reviewed: { label: '已复核待发布', tone: 'reviewed' },
  draft: { label: '待复核', tone: 'reviewed' },
  missing: { label: '缺少同日数据', tone: 'missing' }
})

const OBSERVATION_STATES = new Set(['observe', 'observing', 'observation', 'shadow'])

function finiteNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function metricEntry(source, code) {
  const entry = source?.[code]
  return entry && typeof entry === 'object' ? entry : null
}

function metricValue(source, code) {
  return finiteNumber(metricEntry(source, code)?.value)
}

function normalizedText(value) {
  return value === undefined || value === null ? '' : String(value).trim()
}

function presentationContributions(snapshot) {
  const serverPresentation = snapshot?.presentation?.contributions
  if (Array.isArray(serverPresentation)) return serverPresentation
  return []
}

function operatingGate(item) {
  const gate = item?.operating_gate
  return gate && typeof gate === 'object' ? gate : null
}

function gateCardStatus(gate, fallback) {
  if (!gate) return fallback
  const state = normalizedText(gate.state).toLowerCase()
  if (OBSERVATION_STATES.has(state)) {
    return fallback.tone === 'missing'
      ? { label: '缺少同日数据 · 观察态', tone: 'missing' }
      : { label: '观察态 · 不计入合计', tone: 'reviewed' }
  }
  if (state === 'aggregate_only' || (gate.presentation_enabled === false && gate.contribution_enabled === true)) {
    return { label: '仅参与合计 · 未展示', tone: 'reviewed' }
  }
  if (state === 'closed' || (gate.presentation_enabled === false && gate.contribution_enabled === false)) {
    return { label: '暂不展示 · 未计入合计', tone: fallback.tone === 'missing' ? 'missing' : 'reviewed' }
  }
  if (gate.contribution_enabled === false) {
    return fallback.tone === 'missing'
      ? { label: '缺少同日数据 · 不计入合计', tone: 'missing' }
      : { label: '不计入合计', tone: 'reviewed' }
  }
  return fallback
}

function combinedReason(item, gate) {
  const reasons = [gate?.reason, item?.reason]
    .map(normalizedText)
    .filter(Boolean)
  return [...new Set(reasons)].join(' · ')
}

function formatNumber(value, digits = 0) {
  const number = finiteNumber(value)
  if (number === null) return '--'
  return number.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  })
}

function formatWan(value) {
  const number = finiteNumber(value)
  if (number === null) return '--'
  const wan = number / 10000
  return `${formatNumber(wan, Math.abs(wan) >= 1000 ? 0 : 1)} 万`
}

function formatPercent(value) {
  const number = finiteNumber(value)
  if (number === null) return '--'
  return `${formatNumber(number, 1)}%`
}

function coverageHint(entry, snapshot) {
  const coverage = entry?.coverage || {}
  const authorized = finiteNumber(
    coverage.authorized_buildings ?? snapshot?.coverage?.authorized_buildings
  )
  const included = finiteNumber(coverage.included_buildings)
  if (authorized !== null && included !== null) return `${included}/${authorized} 栋纳入`
  if (entry?.unavailable_reason) return '当前口径不可用'
  return '等待覆盖信息'
}

function buildKpi(snapshot, definition, options = {}) {
  const entry = metricEntry(snapshot?.metrics, definition.code)
  const raw = metricValue(snapshot?.metrics, definition.code)
  const allowBusinessNavigation = options.allowBusinessNavigation !== false
  return {
    key: definition.code,
    label: definition.label,
    value: definition.format(raw),
    tone: definition.tone,
    target: allowBusinessNavigation ? (METRIC_PAGE[definition.code] || null) : null,
    available: raw !== null,
    hint: coverageHint(entry, snapshot),
    unavailableReason: entry?.unavailable_reason || ''
  }
}

export function buildPortfolioKpis(snapshot = {}, options = {}) {
  const authorized = finiteNumber(snapshot?.coverage?.authorized_buildings)
  const sameDaySnapshots = finiteNumber(
    snapshot?.coverage?.same_day_snapshot_buildings
    ?? snapshot?.coverage?.included_same_day_buildings
  )
  const buildingCount = metricValue(snapshot?.metrics, 'portfolio.building_count') ?? authorized
  const buildingHint = authorized === null
    ? '等待授权范围'
    : `${sameDaySnapshots ?? 0}/${authorized} 栋同日有快照`

  return [
    {
      key: 'portfolio.building_count',
      label: '授权楼宇',
      value: buildingCount === null ? '--' : `${formatNumber(buildingCount)} 栋`,
      tone: 'cyan',
      target: null,
      available: buildingCount !== null,
      hint: buildingHint,
      unavailableReason: ''
    },
    buildKpi(snapshot, { code: 'area.rentable_sqm', label: '可出租面积', format: (value) => value === null ? '--' : `${formatNumber(value)} ㎡`, tone: 'blue' }, options),
    buildKpi(snapshot, { code: 'area.leased_sqm', label: '在租面积', format: (value) => value === null ? '--' : `${formatNumber(value)} ㎡`, tone: 'green' }, options),
    buildKpi(snapshot, { code: 'occupancy.area_rate', label: '面积出租率', format: formatPercent, tone: 'blue' }, options),
    buildKpi(snapshot, { code: 'rent.effective_daily', label: '有效平均日租', format: (value) => value === null ? '--' : `${formatNumber(value, 2)} 元`, tone: 'amber' }, options),
    buildKpi(snapshot, { code: 'billing.receivable_month', label: '本月应收', format: formatWan, tone: 'cyan' }, options),
    buildKpi(snapshot, { code: 'collection.rate_month', label: '本月收缴率', format: formatPercent, tone: 'green' }, options),
    buildKpi(snapshot, { code: 'arrears.balance', label: '逾期余额', format: formatWan, tone: 'red' }, options),
    buildKpi(snapshot, { code: 'expiry.area_pressure_365d', label: '未来一年到期压力', format: formatPercent, tone: 'amber' }, options)
  ]
}

function buildingMetric(metrics, definition, options = {}) {
  const entry = metricEntry(metrics, definition.code)
  const raw = metricValue(metrics, definition.code)
  const coverage = entry?.coverage || {}
  const available = raw !== null && !entry?.unavailable_reason
  return {
    key: definition.code,
    label: definition.label,
    value: definition.format(available ? raw : null),
    target: options.allowBusinessNavigation === false ? null : (METRIC_PAGE[definition.code] || null),
    available,
    freshness: coverage.freshness_status || 'unknown',
    completeness: coverage.completeness_status || 'unknown',
    unavailableReason: entry?.unavailable_reason || ''
  }
}

function buildBuildingCards(snapshot, contributions, options = {}) {
  return contributions
    .map((item) => {
      const building = item?.building || {}
      const gate = operatingGate(item)
      const publicationStatus = STATUS_COPY[item?.publication_status] || STATUS_COPY.missing
      const status = gateCardStatus(gate, publicationStatus)
      const metrics = item?.metrics || {}
      const rows = item?._suppress_metrics === true
        ? []
        : [
            buildingMetric(metrics, { code: 'occupancy.area_rate', label: '出租率', format: formatPercent }, options),
            buildingMetric(metrics, { code: 'rent.effective_daily', label: '有效日租', format: (value) => value === null ? '--' : `${formatNumber(value, 2)} 元` }, options),
            buildingMetric(metrics, { code: 'collection.rate_month', label: '收缴率', format: formatPercent }, options),
            buildingMetric(metrics, { code: 'expiry.area_pressure_365d', label: '一年到期压力', format: formatPercent }, options),
            buildingMetric(metrics, { code: 'risk.open_items', label: '未关闭风险', format: (value) => value === null ? '--' : `${formatNumber(value)} 项` }, options)
          ]
      const availableCount = rows.filter((row) => row.available).length
      return {
        key: building.code || item?.snapshot_id || building.name,
        code: building.code || '',
        name: building.name || building.code || '未命名楼宇',
        statusLabel: status.label,
        statusTone: status.tone,
        publicationStatus: item?.publication_status || 'missing',
        reason: combinedReason(item, gate),
        publishedAt: item?.published_at || '',
        asOfDate: snapshot?.as_of_date || '',
        presentationEnabled: gate?.presentation_enabled,
        contributionEnabled: gate?.contribution_enabled,
        operatingState: normalizedText(gate?.state).toLowerCase(),
        gateRevision: gate?.revision ?? null,
        gateEventId: gate?.event_id ?? null,
        availableCount,
        metricCount: rows.length,
        metrics: rows
      }
    })
}

export function buildPortfolioBuildingCards(snapshot = {}, options = {}) {
  return buildBuildingCards(snapshot, presentationContributions(snapshot), options)
}

export function buildPortfolioQualityBuildingCards(snapshot = {}, options = {}) {
  const contributions = Array.isArray(snapshot?.contributions) ? snapshot.contributions : []
  return buildBuildingCards(snapshot, contributions, options)
}

function leadershipRolloutPlaceholder(item = {}) {
  const state = normalizedText(item?.state).toLowerCase() || 'unknown'
  const presentationEnabled = item?.presentation_enabled === true
  const contributionEnabled = item?.contribution_enabled === true
  const reason = state === 'aggregate_only'
    ? '该楼宇仅参与板块合计，不提供逐楼概况'
    : state === 'closed'
    ? '该楼宇暂不展示，且不计入板块合计'
    : '该楼宇当前不提供逐楼概况'
  return {
    building: {
      code: normalizedText(item?.building_code),
      name: normalizedText(item?.building_name) || normalizedText(item?.building_code)
    },
    publication_status: 'withheld',
    presentation_enabled: presentationEnabled,
    contribution_enabled: contributionEnabled,
    operating_gate: {
      state,
      presentation_enabled: presentationEnabled,
      contribution_enabled: contributionEnabled
    },
    reason,
    _suppress_metrics: true,
    metrics: {}
  }
}

export function buildLeadershipOverviewBuildingCards(snapshot = {}, options = {}) {
  const rolloutItems = Array.isArray(snapshot?.rollout?.items) ? snapshot.rollout.items : []
  if (!rolloutItems.length) {
    return buildBuildingCards(snapshot, presentationContributions(snapshot), options)
  }

  const contributions = [
    ...(Array.isArray(snapshot?.contributions) ? snapshot.contributions : []),
    ...presentationContributions(snapshot)
  ]
  const contributionByCode = new Map()
  for (const contribution of contributions) {
    const code = normalizedText(contribution?.building?.code).toLowerCase()
    if (code && !contributionByCode.has(code)) contributionByCode.set(code, contribution)
  }

  const includedCodes = new Set()
  const safeRows = rolloutItems.map((item) => {
    const code = normalizedText(item?.building_code).toLowerCase()
    if (code) includedCodes.add(code)
    if (item?.presentation_enabled === true && contributionByCode.has(code)) {
      return contributionByCode.get(code)
    }
    return leadershipRolloutPlaceholder(item)
  })

  for (const contribution of presentationContributions(snapshot)) {
    const code = normalizedText(contribution?.building?.code).toLowerCase()
    if (!code || includedCodes.has(code)) continue
    safeRows.push(contribution)
  }
  return buildBuildingCards(snapshot, safeRows, options)
}

export function buildPortfolioHealth(
  snapshot = {},
  buildingCards = buildPortfolioQualityBuildingCards(snapshot)
) {
  const authorized = finiteNumber(snapshot?.coverage?.authorized_buildings) ?? buildingCards.length
  const sameDaySnapshots = finiteNumber(
    snapshot?.coverage?.same_day_snapshot_buildings
    ?? snapshot?.coverage?.included_same_day_buildings
  )
    ?? buildingCards.filter((item) => item.publicationStatus !== 'missing').length
  const complete = buildingCards.filter((item) => (
    item.metricCount > 0 && item.availableCount === item.metricCount
  )).length
  const missing = Math.max(authorized - sameDaySnapshots, 0)
  const observed = buildingCards.filter((item) => OBSERVATION_STATES.has(item.operatingState)).length
  const notIncluded = buildingCards.filter((item) => (
    item.presentationEnabled !== false && item.contributionEnabled === false
  )).length
  const rolloutItems = Array.isArray(snapshot?.rollout?.items) ? snapshot.rollout.items : []
  const aggregateOnly = rolloutItems.filter((item) => (
    item?.presentation_enabled === false && item?.contribution_enabled === true
  )).length
  const closed = rolloutItems.filter((item) => (
    item?.presentation_enabled === false && item?.contribution_enabled === false
  )).length
  const dataClassification = normalizedText(
    snapshot?.data_classification ?? snapshot?.presentation?.data_classification
  ).toLowerCase()
  return {
    authorized,
    sameDaySnapshots,
    complete,
    missing,
    observed,
    notIncluded,
    aggregateOnly,
    closed,
    dataClassification,
    simulated: dataClassification === 'simulated',
    asOfDate: snapshot?.as_of_date || '--',
    sourceObservedAt: snapshot?.timestamps?.source_observed_at || '',
    publishedAt: snapshot?.timestamps?.published_at || ''
  }
}

export function selectPortfolioOverviewSnapshot(snapshot = {}, buildingCode = '') {
  const normalizedCode = normalizedText(buildingCode).toLowerCase()
  if (!normalizedCode) return snapshot
  const presented = presentationContributions(snapshot)
  const contribution = presented.find((item) => (
    normalizedText(item?.building?.code).toLowerCase() === normalizedCode
  ))
  if (!contribution) return snapshot

  const building = contribution?.building || {}
  const code = normalizedText(building.code)
  const name = normalizedText(building.name) || code
  const rolloutItems = Array.isArray(snapshot?.rollout?.items) ? snapshot.rollout.items : []
  const selectedRollout = rolloutItems.filter((item) => (
    normalizedText(item?.building_code).toLowerCase() === normalizedCode
  ))
  return {
    ...snapshot,
    scope: {
      scope_type: 'building',
      scope_code: code,
      display_name: name,
      building_codes: [code],
      building: {
        code,
        name,
        ...(building?.operations_profile ? { operations_profile: building.operations_profile } : {})
      },
      ...(building?.operations_profile ? { operations_profile: building.operations_profile } : {})
    },
    coverage: {
      ...(snapshot?.coverage || {}),
      portfolio_buildings: 1,
      authorized_buildings: 1,
      presentation_enabled_buildings: 1,
      contribution_enabled_buildings: contribution?.contribution_enabled ? 1 : 0,
      published_same_day_buildings: contribution?.publication_status === 'published' ? 1 : 0,
      live_adapter_buildings: contribution?.publication_status === 'live_adapter' ? 1 : 0,
      same_day_snapshot_buildings: contribution?.publication_status === 'missing' ? 0 : 1,
      included_same_day_buildings: contribution?.contribution_enabled ? 1 : 0
    },
    metrics: contribution?.metrics || {},
    contributions: [contribution],
    rollout: {
      ...(snapshot?.rollout || {}),
      authorized_buildings: 1,
      presentation_enabled_buildings: 1,
      contribution_enabled_buildings: contribution?.contribution_enabled ? 1 : 0,
      items: selectedRollout
    },
    presentation: {
      ...(snapshot?.presentation || {}),
      trend: [],
      contributions: [contribution]
    }
  }
}

export function buildLeadershipPresentationState(snapshot = {}) {
  const metrics = snapshot?.metrics || {}
  const value = (code) => metricValue(metrics, code)
  const receivable = value('billing.receivable_month')
  const received = value('collection.received_month')
  const unpaid = receivable !== null && received !== null
    ? Math.max(receivable - received, 0)
    : null
  const collectionRate = value('collection.rate_month')
  const arrears = value('arrears.balance')

  return {
    stats: {
      receivables: {
        total_rent: receivable,
        total_received: received,
        total_balance: unpaid,
        total_unpaid_balance: unpaid,
        total_overdue_balance: arrears,
        received_rate: collectionRate
      },
      projection: { source: 'portfolio-overview-v1', detail_lists_available: false }
    },
    operations: {
      metrics: {
        current_month_receivable: receivable,
        current_month_received: received,
        current_month_unpaid_balance: unpaid,
        current_month_overdue_balance: arrears,
        overdue_balance: arrears,
        received_rate: collectionRate,
        vacancy_area: value('vacancy.area_sqm'),
        vacancy_room_count: null,
        expiring_contract_count_90: value('expiry.contract_count_90d'),
        due_follow_up_customer_count: null,
        unpaid_deposit_amount: null,
        unpaid_deposit_count: null,
        missing_contract_body_count: null,
        risk_count: value('risk.open_items'),
        high_risk_count: null
      },
      vacancy: {
        room_count: null,
        area: value('vacancy.area_sqm'),
        total_rooms: null,
        total_area: value('area.rentable_sqm'),
        occupancy_rate: value('occupancy.area_rate')
      },
      queue_counts: {},
      todos: [],
      projection: { source: 'portfolio-overview-v1', detail_lists_available: false }
    }
  }
}

export function presentationMetricTarget(code) {
  return METRIC_PAGE[code] || null
}
