import assert from 'node:assert/strict'

import {
  buildAuthorizedPortfolioOverviewOptions,
  buildAuthorizedPropertyOptions,
  buildScopedApiUrl,
  buildScopedDownloadUrl,
  getPropertyScopeContext,
  propertyScopeNamespace,
  resetPropertyScopeContext,
  resolveAuthorizedPortfolioOverviewSelection,
  resolveAuthorizedPropertySelection,
  setPropertyScopeContext
} from '../src/lib/propertyScope.js'
import { api, openDownload } from '../src/lib/api.js'
import { cachedApi, invalidateApiCache } from '../src/lib/prefetchCache.js'

const originalFetch = globalThis.fetch
const originalWindow = globalThis.window
const hadWindow = Object.prototype.hasOwnProperty.call(globalThis, 'window')
const requests = []
const downloadWindows = []
const responseGates = new Map()

globalThis.window = {
  location: { origin: 'http://lease-manager.local' },
  open: (...args) => downloadWindows.push(args)
}

globalThis.fetch = async (path) => {
  const normalizedPath = String(path)
  requests.push(normalizedPath)
  const requestNumber = requests.length
  const gate = responseGates.get(normalizedPath)
  if (gate) await gate
  const payload = { requestNumber, path: normalizedPath }
  return {
    status: 200,
    ok: true,
    headers: { get: () => 'application/json' },
    json: async () => payload,
    text: async () => JSON.stringify(payload)
  }
}

try {
  resetPropertyScopeContext()
  setPropertyScopeContext({
    user_id: 11,
    authorization_version: 'grant-v1',
    gate_policy_version: 'gate-v1',
    scope_type: 'building',
    scope_code: 'DEMO_A',
    metric_definition_version: 'OPS_V1'
  })

  assert.deepEqual(getPropertyScopeContext(), {
    userId: '11',
    authorizationVersion: 'grant-v1',
    gatePolicyVersion: 'gate-v1',
    scopeType: 'building',
    scopeCode: 'DEMO_A',
    metricDefinition: 'OPS_V1'
  })

  assert.equal(
    buildScopedApiUrl('/api/contracts?month=2026-08&building=OLD#rows'),
    '/api/contracts?month=2026-08&building=DEMO_A#rows'
  )
  assert.equal(
    buildScopedDownloadUrl('/api/contracts/export/excel?keyword=%E4%B8%8A%E6%B5%B7'),
    '/api/contracts/export/excel?keyword=%E4%B8%8A%E6%B5%B7&building=DEMO_A'
  )
  openDownload('/api/rent-analytics/export?start_date=2026-01-01&building=OTHER')
  assert.deepEqual(downloadWindows.at(-1), [
    '/api/rent-analytics/export?start_date=2026-01-01&building=DEMO_A',
    '_blank',
    'noopener,noreferrer'
  ])
  openDownload('/api/rent-analytics/export?start_date=2026-01-01&building=OTHER', { includeScope: false })
  assert.deepEqual(downloadWindows.at(-1), [
    '/api/rent-analytics/export?start_date=2026-01-01&building=OTHER',
    '_blank',
    'noopener,noreferrer'
  ])

  for (const authPath of [
    '/api/auth/status?source=boot',
    '/api/auth/login',
    '/api/auth/me',
    '/api/auth/logout',
    '/api/auth/change-password'
  ]) {
    assert.equal(buildScopedApiUrl(authPath), authPath)
  }
  for (const controlPath of [
    '/api/buildings',
    '/api/buildings/catalog',
    '/api/audit/platform',
    '/api/audit/platform/logs',
    '/api/auth/authorizations/users/11',
    '/api/version',
    '/api/system/health',
    '/api/settings/feishu',
    '/api/backups'
  ]) {
    assert.equal(buildScopedApiUrl(controlPath), controlPath)
  }
  assert.equal(
    buildScopedApiUrl('/api/audit/platform/logs?page=1&building=DEMO_A&portfolio=SCI-FIN'),
    '/api/audit/platform/logs?page=1'
  )
  assert.equal(buildScopedApiUrl('/health?probe=1'), '/health?probe=1')
  assert.equal(buildScopedApiUrl('/api/audit/logs?page=1'), '/api/audit/logs?page=1&building=DEMO_A')

  const scopeSummary = {
    items: [
      { code: 'DEMO_A', name: '示例双塔中心' },
      { code: 'FIFTH', name: '第五楼' },
      { code: 'CANDIDATE', name: '候选楼宇', is_provisional: true }
    ],
    default: { code: 'DEMO_A' },
    portfolio: {
      items: [{ code: 'SCI-FIN', name: '示例资产组合' }],
      default: { code: 'SCI-FIN' }
    },
    portfolio_overview: {
      items: [{ code: 'LEADER-VIEW', name: '示例资产组合', building_codes: ['DEMO_A', 'FIFTH'] }],
      default: { code: 'LEADER-VIEW' }
    }
  }
  assert.deepEqual(buildAuthorizedPropertyOptions(scopeSummary), [
    { value: 'portfolio:SCI-FIN', label: '板块 · 示例资产组合' },
    { value: 'building:DEMO_A', label: '示例双塔中心' },
    { value: 'building:FIFTH', label: '第五楼' }
  ])
  assert.deepEqual(
    resolveAuthorizedPropertySelection(scopeSummary, { building: 'fifth' }),
    { type: 'building', code: 'FIFTH' }
  )
  assert.deepEqual(
    resolveAuthorizedPropertySelection(scopeSummary, { portfolio: 'sci-fin' }),
    { type: 'portfolio', code: 'SCI-FIN' }
  )
  assert.deepEqual(
    resolveAuthorizedPropertySelection(scopeSummary, { building: 'OLD-USER-BUILDING' }),
    { type: 'building', code: 'DEMO_A' }
  )
  assert.deepEqual(
    resolveAuthorizedPropertySelection(scopeSummary, { building: 'CANDIDATE' }),
    { type: 'building', code: 'DEMO_A' }
  )
  assert.deepEqual(
    resolveAuthorizedPropertySelection(scopeSummary, { building: 'DEMO_A', portfolio: 'SCI-FIN' }),
    { type: 'building', code: 'DEMO_A' }
  )
  assert.deepEqual(buildAuthorizedPortfolioOverviewOptions(scopeSummary), [
    { value: 'portfolio_overview:LEADER-VIEW', label: '板块概况 · 示例资产组合' }
  ])
  assert.deepEqual(
    resolveAuthorizedPortfolioOverviewSelection(scopeSummary, { overview_portfolio: 'leader-view' }),
    { type: 'portfolio_overview', code: 'LEADER-VIEW' }
  )
  assert.deepEqual(
    resolveAuthorizedPortfolioOverviewSelection(scopeSummary, { overview_portfolio: 'UNKNOWN' }),
    { type: 'portfolio_overview', code: 'LEADER-VIEW' }
  )
  assert.deepEqual(
    resolveAuthorizedPortfolioOverviewSelection({
      portfolio_overview: {
        items: [{ code: 'LEADER-VIEW' }, { code: 'SECOND-OVERVIEW' }],
        default: { code: 'LEADER-VIEW' }
      }
    }, { portfolio: 'SECOND-OVERVIEW' }),
    { type: 'portfolio_overview', code: 'LEADER-VIEW' }
  )

  const firstNamespace = propertyScopeNamespace()
  const first = await cachedApi('/api/contracts?page=1')
  const repeated = await cachedApi('/api/contracts?page=1')
  assert.equal(first.requestNumber, 1)
  assert.equal(repeated.requestNumber, 1)
  assert.equal(requests.length, 1)
  assert.equal(requests[0], '/api/contracts?page=1&building=DEMO_A')

  setPropertyScopeContext({
    userId: 11,
    authorizationVersion: 'grant-v1',
    gatePolicyVersion: 'gate-v1',
    scopeType: 'building',
    scopeCode: 'OTHER',
    metricDefinition: 'OPS_V1'
  })
  assert.notEqual(propertyScopeNamespace(), firstNamespace)
  const otherBuilding = await cachedApi('/api/contracts?page=1')
  assert.equal(otherBuilding.requestNumber, 2)
  assert.equal(requests[1], '/api/contracts?page=1&building=OTHER')

  setPropertyScopeContext({
    userId: 22,
    authorizationVersion: 'grant-v1',
    gatePolicyVersion: 'gate-v1',
    scopeType: 'building',
    scopeCode: 'OTHER',
    metricDefinition: 'OPS_V1'
  })
  const otherUser = await cachedApi('/api/contracts?page=1')
  assert.equal(otherUser.requestNumber, 3)

  setPropertyScopeContext({
    userId: 22,
    authorizationVersion: 'grant-v2',
    gatePolicyVersion: 'gate-v1',
    scopeType: 'building',
    scopeCode: 'OTHER',
    metricDefinition: 'OPS_V1'
  })
  const changedAuthorization = await cachedApi('/api/contracts?page=1')
  assert.equal(changedAuthorization.requestNumber, 4)

  const authorizationNamespace = propertyScopeNamespace()
  setPropertyScopeContext({
    userId: 22,
    authorizationVersion: 'grant-v2',
    gatePolicyVersion: 'gate-v2',
    scopeType: 'building',
    scopeCode: 'OTHER',
    metricDefinition: 'OPS_V1'
  })
  assert.notEqual(propertyScopeNamespace(), authorizationNamespace)
  const changedGatePolicy = await cachedApi('/api/contracts?page=1')
  assert.equal(changedGatePolicy.requestNumber, 5)

  setPropertyScopeContext({
    userId: 22,
    authorizationVersion: 'grant-v2',
    gatePolicyVersion: 'gate-v2',
    scopeType: 'building',
    scopeCode: 'OTHER',
    metricDefinition: 'OPS_V2'
  })
  const changedMetric = await cachedApi('/api/contracts?page=1')
  assert.equal(changedMetric.requestNumber, 6)

  invalidateApiCache('/api/contracts')
  const afterPrefixReset = await cachedApi('/api/contracts?page=1')
  assert.equal(afterPrefixReset.requestNumber, 7)

  resetPropertyScopeContext()
  assert.deepEqual(getPropertyScopeContext(), {
    userId: '',
    authorizationVersion: '',
    gatePolicyVersion: '',
    scopeType: '',
    scopeCode: '',
    metricDefinition: ''
  })
  const afterLogoutReset = await cachedApi('/api/contracts?page=1')
  assert.equal(afterLogoutReset.requestNumber, 8)
  assert.equal(requests.at(-1), '/api/contracts?page=1')

  invalidateApiCache()
  const afterExplicitLoginReset = await cachedApi('/api/contracts?page=1')
  assert.equal(afterExplicitLoginReset.requestNumber, 9)

  setPropertyScopeContext({
    userId: 33,
    authorizationVersion: 'grant-v3',
    gatePolicyVersion: 'gate-v3',
    scopeType: 'building',
    scopeCode: 'DEMO_A',
    metricDefinition: 'OPS_V2'
  })

  let releaseRooms
  const roomsGate = new Promise((resolve) => { releaseRooms = resolve })
  const scopedRoomsPath = '/api/rooms?slow=unrelated&building=DEMO_A'
  responseGates.set(scopedRoomsPath, roomsGate)
  const pendingRooms = cachedApi('/api/rooms?slow=unrelated')
  await Promise.resolve()
  const roomsRequestCount = requests.length
  invalidateApiCache('/api/contracts')
  releaseRooms()
  const completedRooms = await pendingRooms
  const cachedRooms = await cachedApi('/api/rooms?slow=unrelated')
  assert.equal(cachedRooms.requestNumber, completedRooms.requestNumber)
  assert.equal(requests.length, roomsRequestCount)
  responseGates.delete(scopedRoomsPath)

  let releaseContracts
  const contractsGate = new Promise((resolve) => { releaseContracts = resolve })
  const scopedContractsPath = '/api/contracts?slow=matching&building=DEMO_A'
  responseGates.set(scopedContractsPath, contractsGate)
  const pendingContracts = cachedApi('/api/contracts?slow=matching')
  await Promise.resolve()
  invalidateApiCache('/api/contracts')
  releaseContracts()
  const invalidatedContracts = await pendingContracts
  responseGates.delete(scopedContractsPath)
  const fetchedAgainContracts = await cachedApi('/api/contracts?slow=matching')
  assert.notEqual(fetchedAgainContracts.requestNumber, invalidatedContracts.requestNumber)

  invalidateApiCache()
  await cachedApi('/api/audit/platform/logs?page=1')
  assert.equal(requests.at(-1), '/api/audit/platform/logs?page=1')

  await api('/api/operations/portfolio-overview?portfolio=LEADER-VIEW', { includeScope: false })
  assert.equal(
    requests.at(-1),
    '/api/operations/portfolio-overview?portfolio=LEADER-VIEW'
  )
} finally {
  invalidateApiCache()
  resetPropertyScopeContext()
  globalThis.fetch = originalFetch
  if (hadWindow) globalThis.window = originalWindow
  else delete globalThis.window
}
