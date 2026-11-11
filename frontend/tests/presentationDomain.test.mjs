import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildLeadershipPresentationState,
  buildLeadershipOverviewBuildingCards,
  buildPortfolioBuildingCards,
  buildPortfolioHealth,
  buildPortfolioKpis,
  buildPortfolioQualityBuildingCards,
  presentationMetricTarget,
  selectPortfolioOverviewSnapshot
} from '../src/lib/presentationDomain.js'

function metric(value, coverage = {}) {
  return { value, coverage: { authorized_buildings: 2, included_buildings: value == null ? 0 : 2, ...coverage } }
}

test('portfolio KPIs preserve missing values instead of displaying zero', () => {
  const cards = buildPortfolioKpis({
    coverage: { authorized_buildings: 2, included_same_day_buildings: 1 },
    metrics: {
      'portfolio.building_count': metric(2),
      'area.rentable_sqm': metric(null),
      'area.leased_sqm': metric(0),
      'occupancy.area_rate': metric(0.8),
      'billing.receivable_month': metric(12860000),
      'collection.rate_month': metric(0)
    }
  })

  assert.equal(cards.find((item) => item.key === 'area.rentable_sqm').value, '--')
  assert.equal(cards.find((item) => item.key === 'area.leased_sqm').value, '0 ㎡')
  assert.equal(cards.find((item) => item.key === 'occupancy.area_rate').value, '0.8%')
  assert.equal(cards.find((item) => item.key === 'billing.receivable_month').value, '1,286 万')
  assert.equal(cards.find((item) => item.key === 'collection.rate_month').value, '0.0%')
  assert.equal(cards[0].hint, '1/2 栋同日有快照')
})

test('building cards use only authorized contributions and keep missing buildings explicit', () => {
  const snapshot = {
    as_of_date: '2026-08-05',
    coverage: { authorized_buildings: 2, included_same_day_buildings: 1 },
    contributions: [
      {
        building: { code: 'DEMO_A', name: 'DEMO_A' },
        publication_status: 'live_adapter',
        metrics: {
          'occupancy.area_rate': metric(91.2),
          'rent.effective_daily': metric(7.36),
          'collection.rate_month': metric(99.5),
          'expiry.area_pressure_365d': {
            ...metric(12.5, { freshness_status: 'stale' }),
            unavailable_reason: '指标已过期'
          },
          'risk.open_items': metric(3)
        }
      },
      {
        building: { code: 'DEMO_B', name: '示例主裙楼中心' },
        publication_status: 'missing',
        reason: '该统计日没有当前已发布快照',
        metrics: {}
      }
    ]
  }
  snapshot.presentation = { contributions: snapshot.contributions }

  const cards = buildPortfolioBuildingCards(snapshot)
  assert.deepEqual(cards.map((item) => item.code), ['DEMO_A', 'DEMO_B'])
  assert.equal(cards[0].statusLabel, '实时明细')
  assert.equal(cards[0].metrics[0].value, '91.2%')
  assert.equal(cards[0].metrics[3].value, '--')
  assert.equal(cards[0].metrics[3].available, false)
  assert.equal(cards[1].statusLabel, '缺少同日数据')
  assert.equal(cards[1].metrics.every((item) => item.value === '--'), true)
  assert.equal(cards[0].asOfDate, '2026-08-05')

  const health = buildPortfolioHealth(snapshot, cards)
  assert.deepEqual(health, {
    authorized: 2,
    sameDaySnapshots: 1,
    complete: 0,
    missing: 1,
    observed: 0,
    notIncluded: 0,
    aggregateOnly: 0,
    closed: 0,
    dataClassification: '',
    simulated: false,
    asOfDate: '2026-08-05',
    sourceObservedAt: '',
    publishedAt: ''
  })
})

test('building cards trust server presentation contributions and expose simulated gate state', () => {
  const hiddenAggregateOnly = {
    building: { code: 'HIDDEN', name: '仅参与合计楼宇' },
    publication_status: 'published',
    operating_gate: {
      presentation_enabled: false,
      contribution_enabled: true,
      state: 'aggregate_only'
    },
    metrics: { 'occupancy.area_rate': metric(100) }
  }
  const observed = {
    building: { code: 'OBSERVE', name: '观察楼宇' },
    publication_status: 'published',
    operating_gate: {
      presentation_enabled: true,
      contribution_enabled: false,
      state: 'observe',
      reason: '模拟数据先观察，不计入板块合计',
      revision: 2,
      event_id: 17
    },
    metrics: {
      'occupancy.area_rate': metric(88.6),
      'rent.effective_daily': metric(null)
    }
  }
  const snapshot = {
    as_of_date: '2026-08-05',
    data_classification: 'simulated',
    coverage: { authorized_buildings: 2, same_day_snapshot_buildings: 1 },
    contributions: [hiddenAggregateOnly, observed],
    presentation: { contributions: [observed] },
    rollout: { items: [hiddenAggregateOnly.operating_gate, observed.operating_gate] }
  }

  const cards = buildPortfolioBuildingCards(snapshot)
  assert.deepEqual(cards.map((item) => item.code), ['OBSERVE'])
  assert.equal(cards[0].statusLabel, '观察态 · 不计入合计')
  assert.equal(cards[0].statusTone, 'reviewed')
  assert.equal(cards[0].metrics[0].value, '88.6%')
  assert.equal(cards[0].metrics[1].value, '--')
  assert.equal(cards[0].contributionEnabled, false)
  assert.equal(cards[0].operatingState, 'observe')
  assert.equal(cards[0].gateRevision, 2)
  assert.equal(cards[0].gateEventId, 17)
  assert.match(cards[0].reason, /不计入板块合计/)

  const health = buildPortfolioHealth(snapshot, cards)
  assert.equal(health.observed, 1)
  assert.equal(health.notIncluded, 1)
  assert.equal(health.aggregateOnly, 1)
  assert.equal(health.closed, 0)
  assert.equal(health.dataClassification, 'simulated')
  assert.equal(health.simulated, true)

  const qualityCards = buildPortfolioQualityBuildingCards(snapshot)
  assert.deepEqual(qualityCards.map((item) => item.code), ['HIDDEN', 'OBSERVE'])
  assert.equal(qualityCards[0].statusLabel, '仅参与合计 · 未展示')

  assert.deepEqual(buildPortfolioBuildingCards({
    presentation: { contributions: [] },
    contributions: [hiddenAggregateOnly]
  }), [])
  assert.deepEqual(buildPortfolioBuildingCards({ contributions: [hiddenAggregateOnly] }), [])

  const missingObserved = {
    ...observed,
    publication_status: 'missing',
    reason: '楼宇开闸策略未允许参与板块合计（状态：observe）；该统计日没有当前已发布快照',
    metrics: {}
  }
  const missingCards = buildPortfolioBuildingCards({
    presentation: { contributions: [missingObserved] },
    contributions: [missingObserved]
  })
  assert.equal(missingCards[0].statusLabel, '缺少同日数据 · 观察态')
  assert.equal(missingCards[0].statusTone, 'missing')
  assert.match(missingCards[0].reason, /没有当前已发布快照/)
})

test('portfolio quality still counts hidden aggregate-only and closed buildings', () => {
  const completeMetrics = {
    'occupancy.area_rate': metric(92),
    'rent.effective_daily': metric(6.8),
    'collection.rate_month': metric(98),
    'expiry.area_pressure_365d': metric(12),
    'risk.open_items': metric(1)
  }
  const hiddenComplete = {
    building: { code: 'AGGREGATE_ONLY', name: '只计合计楼宇' },
    publication_status: 'published',
    operating_gate: {
      presentation_enabled: false,
      contribution_enabled: true,
      state: 'aggregate_only'
    },
    metrics: completeMetrics
  }
  const hiddenMissing = {
    building: { code: 'CLOSED', name: '关闭楼宇' },
    publication_status: 'missing',
    operating_gate: {
      presentation_enabled: false,
      contribution_enabled: false,
      state: 'closed'
    },
    metrics: {}
  }
  const snapshot = {
    coverage: { authorized_buildings: 2, same_day_snapshot_buildings: 1 },
    contributions: [hiddenComplete, hiddenMissing],
    presentation: { contributions: [] },
    rollout: { items: [hiddenComplete.operating_gate, hiddenMissing.operating_gate] }
  }

  assert.deepEqual(buildPortfolioBuildingCards(snapshot), [])
  const qualityCards = buildPortfolioQualityBuildingCards(snapshot)
  const health = buildPortfolioHealth(snapshot, qualityCards)
  assert.equal(qualityCards[0].statusLabel, '仅参与合计 · 未展示')
  assert.equal(qualityCards[1].statusLabel, '暂不展示 · 未计入合计')
  assert.equal(health.complete, 1)
  assert.equal(health.missing, 1)
  assert.equal(health.aggregateOnly, 1)
  assert.equal(health.closed, 1)
  assert.equal(health.notIncluded, 0)
  assert.deepEqual(
    qualityCards.filter((item) => item.publicationStatus === 'missing').map((item) => item.name),
    ['关闭楼宇']
  )
})

test('leadership overview builds hidden-state placeholders from rollout without exposing metrics', () => {
  const visible = {
    building: { code: 'OPEN', name: '开放楼宇' },
    publication_status: 'published',
    operating_gate: {
      state: 'open',
      presentation_enabled: true,
      contribution_enabled: true
    },
    metrics: { 'occupancy.area_rate': metric(92) }
  }
  const accidentallyDetailedHidden = {
    building: { code: 'HIDDEN', name: '仅合计楼宇' },
    publication_status: 'published',
    operating_gate: {
      state: 'aggregate_only',
      presentation_enabled: false,
      contribution_enabled: true
    },
    metrics: { 'occupancy.area_rate': metric(100) }
  }
  const snapshot = {
    contributions: [visible, accidentallyDetailedHidden],
    presentation: { contributions: [visible] },
    rollout: {
      items: [
        { building_code: 'OPEN', building_name: '开放楼宇', state: 'open', presentation_enabled: true, contribution_enabled: true },
        { building_code: 'HIDDEN', building_name: '仅合计楼宇', state: 'aggregate_only', presentation_enabled: false, contribution_enabled: true },
        { building_code: 'CLOSED', building_name: '关闭楼宇', state: 'closed', presentation_enabled: false, contribution_enabled: false }
      ]
    }
  }

  const cards = buildLeadershipOverviewBuildingCards(snapshot, { allowBusinessNavigation: false })
  assert.deepEqual(cards.map((item) => item.code), ['OPEN', 'HIDDEN', 'CLOSED'])
  assert.equal(cards[0].metrics[0].value, '92.0%')
  assert.equal(cards[1].statusLabel, '仅参与合计 · 未展示')
  assert.equal(cards[1].presentationEnabled, false)
  assert.deepEqual(cards[1].metrics, [])
  assert.equal(cards[2].statusLabel, '暂不展示 · 未计入合计')
  assert.deepEqual(cards[2].metrics, [])
})

test('presentation metric targets lead to the existing scoped business pages', () => {
  assert.equal(presentationMetricTarget('occupancy.area_rate'), 'rooms')
  assert.equal(presentationMetricTarget('collection.rate_month'), 'receivables')
  assert.equal(presentationMetricTarget('expiry.area_pressure_365d'), 'contracts')
  assert.equal(presentationMetricTarget('unknown.metric'), null)
})

test('leadership single-building overview is selected only from server presentation contributions', () => {
  const operationsProfile = {
    profile_version: 'building-operations-profile-v1',
    building_code: 'DEMO_A',
    space_structure: { type: 'multi_tower_with_commercial', zones: [] }
  }
  const visible = {
    building: { code: 'DEMO_A', name: '示例双塔中心', operations_profile: operationsProfile },
    publication_status: 'live_adapter',
    presentation_enabled: true,
    contribution_enabled: true,
    operating_gate: {
      state: 'open',
      presentation_enabled: true,
      contribution_enabled: true
    },
    metrics: {
      'occupancy.area_rate': metric(93.2),
      'billing.receivable_month': metric(1200000)
    }
  }
  const hidden = {
    building: { code: 'HIDDEN', name: '未展示楼宇' },
    publication_status: 'published',
    presentation_enabled: false,
    contribution_enabled: true,
    metrics: { 'occupancy.area_rate': metric(80) }
  }
  const overview = {
    contract_version: 'portfolio-overview-v1',
    data_classification: 'simulated',
    as_of_date: '2026-08-05',
    scope: {
      scope_type: 'portfolio',
      scope_code: 'DEMO_OFFICE_PORTFOLIO',
      display_name: '示例资产组合',
      building_codes: ['DEMO_A', 'HIDDEN']
    },
    coverage: { authorized_buildings: 2 },
    metrics: { 'portfolio.building_count': metric(2) },
    contributions: [visible, hidden],
    rollout: {
      items: [
        { building_code: 'DEMO_A', presentation_enabled: true, contribution_enabled: true },
        { building_code: 'HIDDEN', presentation_enabled: false, contribution_enabled: true }
      ]
    },
    presentation: { contributions: [visible], trend: [{ month: '2026-08' }] }
  }

  const single = selectPortfolioOverviewSnapshot(overview, 'demo_a')
  assert.equal(single.scope.scope_type, 'building')
  assert.equal(single.scope.scope_code, 'DEMO_A')
  assert.equal(single.scope.display_name, '示例双塔中心')
  assert.deepEqual(single.scope.building_codes, ['DEMO_A'])
  assert.deepEqual(single.scope.operations_profile, operationsProfile)
  assert.deepEqual(single.scope.building.operations_profile, operationsProfile)
  assert.equal(single.metrics['occupancy.area_rate'].value, 93.2)
  assert.deepEqual(single.presentation.contributions, [visible])
  assert.deepEqual(single.presentation.trend, [])
  assert.deepEqual(single.rollout.items.map((item) => item.building_code), ['DEMO_A'])

  assert.equal(selectPortfolioOverviewSnapshot(overview, 'HIDDEN'), overview)
  assert.equal(selectPortfolioOverviewSnapshot(overview, 'UNKNOWN'), overview)
  assert.equal(overview.scope.scope_type, 'portfolio')
})

test('leadership overview metrics are display-only while ordinary presentation targets remain', () => {
  const contribution = {
    building: { code: 'DEMO_A', name: '示例双塔中心' },
    publication_status: 'published',
    metrics: {
      'occupancy.area_rate': metric(92),
      'rent.effective_daily': metric(7.1),
      'collection.rate_month': metric(99),
      'expiry.area_pressure_365d': metric(14),
      'risk.open_items': metric(1)
    }
  }
  const overview = {
    coverage: { authorized_buildings: 1, same_day_snapshot_buildings: 1 },
    metrics: {
      'area.rentable_sqm': metric(10000),
      'occupancy.area_rate': metric(92),
      'billing.receivable_month': metric(500000)
    },
    presentation: { contributions: [contribution] }
  }

  assert.equal(buildPortfolioKpis(overview).some((item) => item.target), true)
  assert.equal(buildPortfolioBuildingCards(overview)[0].metrics.some((item) => item.target), true)

  const leadershipKpis = buildPortfolioKpis(overview, { allowBusinessNavigation: false })
  const leadershipCards = buildPortfolioBuildingCards(overview, { allowBusinessNavigation: false })
  assert.equal(leadershipKpis.every((item) => item.target === null), true)
  assert.equal(leadershipCards[0].metrics.every((item) => item.target === null), true)
})

test('leadership single-building presentation derives only aggregate display fields', () => {
  const snapshot = {
    metrics: {
      'area.rentable_sqm': metric(10000),
      'area.leased_sqm': metric(9200),
      'vacancy.area_sqm': metric(800),
      'occupancy.area_rate': metric(92),
      'billing.receivable_month': metric(600000),
      'collection.received_month': metric(570000),
      'collection.rate_month': metric(95),
      'arrears.balance': metric(30000),
      'rent.effective_daily': metric(7.2),
      'expiry.contract_count_90d': metric(3),
      'risk.open_items': metric(2)
    }
  }

  const state = buildLeadershipPresentationState(snapshot)
  assert.equal(state.stats.receivables.received_rate, 95)
  assert.deepEqual(state.operations.vacancy, {
    room_count: null,
    area: 800,
    total_rooms: null,
    total_area: 10000,
    occupancy_rate: 92
  })
  assert.equal(state.operations.metrics.current_month_receivable, 600000)
  assert.equal(state.operations.metrics.current_month_received, 570000)
  assert.equal(state.operations.metrics.overdue_balance, 30000)
  assert.equal(state.operations.metrics.expiring_contract_count_90, 3)
  assert.deepEqual(state.operations.todos, [])
  assert.equal(state.operations.projection.detail_lists_available, false)
  assert.equal(Object.hasOwn(state.operations, 'queues'), false)
})
