import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  profileAnalysisSegments,
  profileSpaceOptions,
  roomAnalysisSegment,
  usesLegacyDemoAFloorPlan
} from '../src/lib/roomsDomain.js'
import { normalizeRentAnalytics } from '../src/lib/rentAnalyticsDomain.js'

const wholeBuildingProfile = {
  building_code: 'DEMO_C',
  space_structure: {
    type: 'whole_building',
    zones: [{ code: 'WHOLE_BUILDING', name: '整楼', kind: 'whole_building' }]
  },
  data_granularity: ['building', 'leasable_unit'],
  analysis_groups: [
    { code: 'WHOLE_BUILDING', name: '整楼', zone_codes: [], available: true }
  ]
}

const multiTowerProfile = {
  building_code: 'DEMO_A',
  space_structure: {
    type: 'multi_tower_with_commercial',
    zones: [
      { code: 'SOUTH_OFFICE', name: '南塔', kind: 'office' },
      { code: 'NORTH_OFFICE', name: '北塔', kind: 'office' },
      { code: 'COMMERCIAL_SUPPORT', name: '商业配套', kind: 'commercial' }
    ]
  },
  data_granularity: ['building', 'zone', 'floor', 'leasable_unit'],
  analysis_groups: [
    { code: 'WHOLE_BUILDING', name: '整楼', zone_codes: ['SOUTH_OFFICE', 'NORTH_OFFICE', 'COMMERCIAL_SUPPORT'], available: true },
    { code: 'SOUTH_OFFICE', name: '南塔办公', zone_codes: ['SOUTH_OFFICE'], available: true },
    { code: 'NORTH_OFFICE', name: '北塔办公', zone_codes: ['NORTH_OFFICE'], available: true },
    { code: 'COMMERCIAL_SUPPORT', name: '商业配套', zone_codes: ['COMMERCIAL_SUPPORT'], available: true }
  ]
}

const genericMultiZoneProfile = {
  building_code: 'P4_GENERIC_MULTI',
  space_structure: {
    type: 'registered_zones',
    zones: [
      { code: 'EAST', name: '东区', kind: 'office' },
      { code: 'WEST', name: '西区', kind: 'office' }
    ]
  },
  data_granularity: ['building', 'zone', 'leasable_unit'],
  analysis_groups: [
    { code: 'WHOLE_BUILDING', name: '整楼', zone_codes: [], available: true }
  ]
}

test('whole-building profile exposes one real space option and one analysis segment', () => {
  assert.deepEqual(profileSpaceOptions(wholeBuildingProfile), [
    { code: 'WHOLE_BUILDING', value: '整楼', label: '整楼', kind: 'whole_building' }
  ])
  assert.deepEqual(
    profileAnalysisSegments(wholeBuildingProfile).map(({ code, label, filterValue }) => ({ code, label, filterValue })),
    [{ code: 'WHOLE_BUILDING', label: '整楼', filterValue: '整楼' }]
  )
  assert.equal(roomAnalysisSegment({ tower: '整楼', floor: '', room_no: 'A01', room_type: '办公' }, wholeBuildingProfile).label, '整楼')
  assert.equal(usesLegacyDemoAFloorPlan(wholeBuildingProfile), false)
})

test('multi-tower profile keeps the established three detail segments and floor-plan compatibility', () => {
  assert.deepEqual(
    profileAnalysisSegments(multiTowerProfile).map(({ label, filterValue }) => ({ label, filterValue })),
    [
      { label: '南塔办公', filterValue: '南塔' },
      { label: '北塔办公', filterValue: '北塔' },
      { label: '商业配套', filterValue: '商业配套' }
    ]
  )
  assert.equal(roomAnalysisSegment({ tower: '南塔', floor: '2', room_no: 'S0201', room_type: '办公' }, multiTowerProfile).label, '商业配套')
  assert.equal(usesLegacyDemoAFloorPlan(multiTowerProfile), true)
})

test('generic multi-zone profile uses every registered zone instead of collapsing to whole building', () => {
  assert.deepEqual(
    profileAnalysisSegments(genericMultiZoneProfile).map(({ code, label, filterValue }) => ({ code, label, filterValue })),
    [
      { code: 'EAST', label: '东区', filterValue: '东区' },
      { code: 'WEST', label: '西区', filterValue: '西区' }
    ]
  )
  assert.equal(roomAnalysisSegment({ tower: '东区' }, genericMultiZoneProfile).label, '东区')
  assert.equal(roomAnalysisSegment({ tower: '西区' }, genericMultiZoneProfile).label, '西区')
})

test('rent analytics preserves a configured whole-building-only segment', () => {
  const normalized = normalizeRentAnalytics({
    segments: [{ key: 'overall', label: '整楼', color: '#1d4ed8' }],
    daily: []
  })

  assert.deepEqual(normalized.segments, [{ key: 'overall', label: '整楼', color: '#1d4ed8' }])
  assert.doesNotMatch(normalized.segments.map((item) => item.label).join(' '), /南塔|北塔/)
})

test('detail views receive the selected operations profile and forms do not hard-code tower options', () => {
  const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  const contractsSource = readFileSync(new URL('../src/views/ContractsView.vue', import.meta.url), 'utf8')
  const formSource = readFileSync(new URL('../src/components/ContractFormModal.vue', import.meta.url), 'utf8')
  const roomsSource = readFileSync(new URL('../src/views/RoomsView.vue', import.meta.url), 'utf8')
  const analyticsSource = readFileSync(new URL('../src/views/OperationsAnalyticsView.vue', import.meta.url), 'utf8')
  const customersSource = readFileSync(new URL('../src/views/CustomersView.vue', import.meta.url), 'utf8')
  const receivablesSource = readFileSync(new URL('../src/views/ReceivablesLedgerView.vue', import.meta.url), 'utf8')
  const customerDetailSource = readFileSync(new URL('../src/components/CustomerDetailModal.vue', import.meta.url), 'utf8')
  const currentPageStart = appSource.indexOf('const currentPage = computed')

  for (const key of ['analytics', 'contracts', 'rooms']) {
    const caseStart = appSource.indexOf(`case '${key}':`, currentPageStart)
    const caseEnd = appSource.indexOf("    case '", caseStart + 8)
    const caseSource = appSource.slice(caseStart, caseEnd === -1 ? undefined : caseEnd)
    assert.match(caseSource, /operationsProfile:\s*selectedBuildingOperationsProfile\.value/)
  }
  assert.match(contractsSource, /:operations-profile="operationsProfile"/)
  assert.doesNotMatch(formSource, /<option value="(?:南塔|北塔|商业配套)">/)
  assert.match(roomsSource, /v-if="showLegacyFloorWorkspace"[^>]+data-testid="rooms-heatmap"/)
  assert.match(roomsSource, /:open="!showLegacyFloorWorkspace"[^>]+data-testid="rooms-ledger-details"/)
  assert.doesNotMatch(analyticsSource, /const towerOrder = \['南塔办公'/)
  assert.match(analyticsSource, /profileAnalysisSegments\(props\.operationsProfile\)/)
  assert.match(analyticsSource, /usesLegacyDemoAFloorPlan\(props\.operationsProfile\)/)
  assert.match(appSource, /portfolio_read_only:\s*true/)
  assert.match(appSource, /propertyScope:\s*selectedPropertyScope\.value/)
  for (const source of [contractsSource, roomsSource, customersSource, receivablesSource, customerDetailSource]) {
    assert.match(source, /propertyScope\?\.type === 'building'/)
  }
})

test('room editor receives the selected profile and derives every space option from it', () => {
  const roomsSource = readFileSync(new URL('../src/views/RoomsView.vue', import.meta.url), 'utf8')
  const roomFormSource = readFileSync(new URL('../src/components/RoomFormModal.vue', import.meta.url), 'utf8')
  const roomFormStart = roomsSource.indexOf('<RoomFormModal')
  const roomFormEnd = roomsSource.indexOf('/>', roomFormStart)
  const roomFormUsage = roomsSource.slice(roomFormStart, roomFormEnd + 2)

  assert.ok(roomFormStart >= 0)
  assert.match(roomFormUsage, /:operations-profile="operationsProfile"/)
  assert.match(roomFormSource, /import \{ profileSpaceOptions \} from '\.\.\/lib\/roomsDomain'/)
  assert.match(roomFormSource, /profileSpaceOptions\(props\.operationsProfile\)/)
  assert.match(roomFormSource, /v-for="option in roomSpaceOptions"/)
  assert.doesNotMatch(roomFormSource, /<option value="(?:南塔|北塔|商业配套)">/)
  assert.doesNotMatch(roomFormSource, /tower:\s*'南塔'/)
})

test('presentation pricing consumes snapshot segment metadata and empty detail facts fail closed', () => {
  const presentationSource = readFileSync(
    new URL('../src/components/PresentationView.vue', import.meta.url),
    'utf8'
  )
  const dashboardSource = readFileSync(
    new URL('../src/views/DashboardView.vue', import.meta.url),
    'utf8'
  )

  assert.match(presentationSource, /priceSegmentDefinitions\.value = Array\.isArray\(price\.segments\)/)
  assert.match(presentationSource, /trendPriceSegments\.value\.map/)
  assert.doesNotMatch(presentationSource, /const SEGMENT_BY_TOWER/)
  assert.doesNotMatch(presentationSource, /const PRICE_SEGMENTS/)
  assert.match(presentationSource, /detail_facts_available !== false/)
  assert.match(presentationSource, /item\.publication_status === 'live_adapter'/)
  assert.match(presentationSource, /明细数据状态/)
  assert.match(dashboardSource, /detail_facts_available !== false/)
  assert.match(dashboardSource, /明细数据状态/)
})
