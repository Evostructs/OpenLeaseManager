import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { parse as parseJavaScript } from '@babel/parser'
import { parse } from '@vue/compiler-sfc'
import { computed, nextTick, ref, watch } from 'vue'

// Execute App's actual entry, request ownership, refresh and authorization
// watcher. Only network transport and unrelated UI helpers are replaced.
const { descriptor } = parse(readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8'))
const script = descriptor.scriptSetup.content
const nodes = parseJavaScript(script, { sourceType: 'module' }).program.body
const functions = [
  'enterPresentation', 'isPresentationEntryCurrent', 'closeNativePresentationFullscreen',
  'refreshLeadershipPresentationContext', 'refreshOperatingGateContext',
  'presentationRefreshIsCurrent', 'refreshedSessionStillActive', 'currentScopeAllowsPresentation',
  'fetchPortfolioOverview', 'requestPortfolioOverview', 'fetchDashboard'
].map(name => {
  const node = nodes.find(item => item.type === 'FunctionDeclaration' && item.id.name === name)
  assert.ok(node, `App function ${name} is available`)
  return script.slice(node.start, node.end)
}).join('\n')
const authorizationWatch = nodes.find(node => node.type === 'ExpressionStatement'
  && node.expression.type === 'CallExpression' && node.expression.callee.name === 'watch'
  && script.slice(node.start, node.end).includes('currentUser.value?.scope?.authorization_version'))
assert.ok(authorizationWatch)
const watcher = script.slice(authorizationWatch.expression.start, authorizationWatch.expression.end)

function harness(t, workspace = 'leadership') {
  const user = () => ({ id: 1, role: 'editor', scope: { authorization_version: 'v1', gate_policy_version: 'g1' }, workspaces: ['leadership', 'operations'] })
  const counters = { overview: 0, dashboard: 0, clearOverview: 0, clearBusiness: 0, configureBusiness: 0, configureOverview: 0 }
  let nextUser = user()
  const dashboardSnapshot = { scope: { scope_type: 'building', scope_code: 'A' }, rollout: { items: [{ building_code: 'A', presentation_enabled: true }] }, legacy_contract_stats: { revenue: 10 }, legacy_operations: {} }
  const context = {
    watch, WORKSPACE_LEADERSHIP: 'leadership', WORKSPACE_OPERATIONS: 'operations',
    currentUser: ref(user()), activeWorkspace: ref(workspace), authSessionGeneration: 1,
    presentationMode: ref(false), presentationWorkspace: ref(''), presentationEntryPending: false,
    presentationEntryGeneration: 0, presentationRefreshGeneration: 0,
    presentationNativeFullscreenEnteredAt: 0, presentationNativeFullscreenCommitted: false,
    presentationPrevTheme: '', selectedOverviewBuilding: ref(''), theme: ref('light'),
    selectedOverviewPortfolio: ref('P'), portfolioOverviewRequestRevision: 0, portfolioOverviewInFlight: null,
    portfolioOverviewSnapshot: ref({}), portfolioOverviewError: ref(''), portfolioOverviewLoading: ref(false),
    dashboardRequestRevision: 0, dashboardLoading: ref(false), operationsLoading: ref(false),
    stats: ref({}), operationsDashboard: ref({}), propertyScopeNamespace: () => 'session1:building:A',
    selectedPropertyScope: ref({ type: 'building', code: 'A' }),
    operatingSnapshot: ref(dashboardSnapshot),
    document: { hidden: false, fullscreenElement: null, documentElement: { async requestFullscreen() {} }, body: { classList: { add() {} } } },
    async api(path) {
      if (path === '/api/auth/me') return structuredClone(nextUser)
      counters.overview += 1
      await nextTick()
      return { contract_version: 'portfolio-overview-v1', scope: { scope_type: 'portfolio', scope_code: 'P' } }
    },
    async cachedApi() { counters.dashboard += 1; await nextTick(); return dashboardSnapshot },
    readHasSession: () => true, scopeQueryText: value => String(value || ''),
    portfolioOverviewRequestPath: code => `/api/overview?portfolio=${code}`,
    resolvedOverviewBuilding: () => '', route: { path: '/overview' }, pathForTab: tab => `/${tab}`,
    availableWorkspacesForUser: value => value.workspaces,
    configurePortfolioOverview() { counters.configureOverview += 1 },
    configurePropertyScope() { counters.configureBusiness += 1; return true },
    clearPortfolioOverviewState() {
      counters.clearOverview += 1
      context.portfolioOverviewRequestRevision += 1
      context.portfolioOverviewSnapshot.value = {}
    },
    clearScopeBoundState() {
      counters.clearBusiness += 1
      context.dashboardRequestRevision += 1
      context.operatingSnapshot.value = {}
    },
    resetPropertyScopeContext() {}, invalidateApiCache() {}, notify() {},
    async changeWorkspace(value) { context.activeWorkspace.value = value },
    syncOverviewRoute() {}, applyTheme() {}, schedulePresentationRefresh() {}
  }
  context.isLeadershipWorkspace = computed(() => context.activeWorkspace.value === 'leadership')
  const app = new Function('context', `with (context) {
    ${functions}
    const stop = ${watcher};
    return { enterPresentation, refreshLeadershipPresentationContext, refreshOperatingGateContext,
      fetchPortfolioOverview, fetchDashboard, stop };
  }`)(context)
  t.after(() => app.stop())
  return { app, context, counters, nextUser(value) { nextUser = value }, user }
}

test('an unchanged /auth/me object replacement emits no competing refresh and enters the leadership cockpit', async t => {
  const { app, context, counters } = harness(t)
  await app.enterPresentation()
  await nextTick()
  assert.equal(counters.overview, 1)
  assert.equal(context.presentationMode.value, true)
  assert.equal(context.presentationWorkspace.value, 'leadership')
  assert.equal(context.presentationEntryPending, false)
})

test('an unchanged authorization response enters the building cockpit with one snapshot request', async t => {
  const { app, context, counters } = harness(t, 'operations')
  await app.enterPresentation()
  await nextTick()
  assert.equal(counters.dashboard, 1)
  assert.equal(context.presentationMode.value, true)
  assert.equal(context.presentationWorkspace.value, 'operations')
})

test('real authorization and gate changes still run scope invalidation and refresh', async t => {
  const { context, counters, user } = harness(t)
  context.currentUser.value = { ...user(), scope: { authorization_version: 'v2', gate_policy_version: 'g1' } }
  await nextTick()
  assert.equal(counters.configureBusiness, 1)
  assert.equal(counters.overview, 1)
  context.currentUser.value.scope.gate_policy_version = 'g2'
  await nextTick()
  assert.equal(counters.configureBusiness, 2)
  assert.equal(counters.overview, 2)
})

test('revocation clears sensitive state and rejects entry without taking a snapshot', async t => {
  const { app, context, counters, nextUser, user } = harness(t)
  nextUser({ ...user(), workspaces: [], scope: { authorization_version: 'revoked', gate_policy_version: 'g2' } })
  await app.enterPresentation()
  await nextTick()
  assert.equal(counters.clearOverview, 1)
  assert.equal(counters.overview, 0)
  assert.equal(context.currentUser.value.scope.authorization_version, 'revoked')
  assert.equal(context.presentationMode.value, false)
})

test('hidden checks publish refreshed permissions without heavy data, and visible refresh fetches once', async t => {
  const { app, context, counters } = harness(t)
  context.presentationMode.value = true
  context.document.hidden = true
  assert.equal(await app.refreshLeadershipPresentationContext({ authorizationOnly: true }), true)
  await nextTick()
  assert.equal(counters.overview, 0)
  context.document.hidden = false
  assert.equal(await app.refreshLeadershipPresentationContext(), true)
  await nextTick()
  assert.equal(counters.overview, 1)
})

test('a new authorization version that retains access enters with one shared overview request', async t => {
  const { app, context, counters, nextUser, user } = harness(t)
  nextUser({ ...user(), scope: { authorization_version: 'v2', gate_policy_version: 'g1' } })
  await app.enterPresentation()
  await nextTick()
  assert.equal(context.currentUser.value.scope.authorization_version, 'v2')
  assert.equal(counters.configureBusiness, 1, 'authorization scope invalidation still runs')
  assert.equal(counters.overview, 1, 'the watcher and entry share the same authorized request')
  assert.equal(context.presentationMode.value, true)
  assert.equal(context.portfolioOverviewInFlight, null)
})

test('leaving presentation during a snapshot discards it and releases the request loading flags', async t => {
  const { app, context } = harness(t, 'operations')
  let finish
  context.cachedApi = () => new Promise(resolve => { finish = resolve })
  let stillVisible = true
  const before = context.operatingSnapshot.value
  const request = app.fetchDashboard({ stillCurrent: () => stillVisible })
  assert.equal(context.dashboardLoading.value, true)
  stillVisible = false
  finish({ scope: { scope_type: 'building', scope_code: 'wrong-after-exit' } })
  assert.equal(await request, false)
  assert.equal(context.operatingSnapshot.value, before)
  assert.equal(context.dashboardLoading.value, false)
  assert.equal(context.operationsLoading.value, false)
})

test('an old dashboard completion cannot clear a newer request loading state', async t => {
  const { app, context } = harness(t, 'operations')
  const finishes = []
  context.cachedApi = () => new Promise(resolve => { finishes.push(resolve) })
  const old = app.fetchDashboard({ stillCurrent: () => false })
  const current = app.fetchDashboard()
  finishes[0]({ legacy_contract_stats: { generation: 'old' } })
  await old
  assert.equal(context.dashboardLoading.value, true)
  assert.equal(context.operationsLoading.value, true)
  finishes[1]({ legacy_contract_stats: { generation: 'current' } })
  await current
  assert.equal(context.stats.value.generation, 'current')
  assert.equal(context.dashboardLoading.value, false)
})

test('overview requests isolate session, authorization, gate, portfolio and invalidated revisions', async t => {
  const { app, context, user } = harness(t)
  const requests = []
  context.api = () => new Promise((resolve, reject) => { requests.push({ resolve, reject }) })
  const first = app.fetchPortfolioOverview()
  assert.equal(app.fetchPortfolioOverview(), first)
  const replacementUser = { ...user(), scope: { authorization_version: 'v2', gate_policy_version: 'g1' } }
  const revised = app.fetchPortfolioOverview({ user: replacementUser })
  assert.notEqual(revised, first)
  const gated = app.fetchPortfolioOverview({ user: { ...replacementUser, scope: { ...replacementUser.scope, gate_policy_version: 'g2' } } })
  assert.notEqual(gated, revised)
  const otherPortfolio = app.fetchPortfolioOverview({ portfolioCode: 'Q' })
  assert.notEqual(otherPortfolio, gated)
  context.authSessionGeneration += 1
  const otherSession = app.fetchPortfolioOverview({ portfolioCode: 'Q' })
  assert.notEqual(otherSession, otherPortfolio)
  context.portfolioOverviewRequestRevision += 1
  const current = app.fetchPortfolioOverview()
  const currentEntry = context.portfolioOverviewInFlight
  for (const request of requests.slice(0, -1)) request.resolve({ contract_version: 'portfolio-overview-v1' })
  await Promise.all([first, revised, gated, otherPortfolio, otherSession])
  assert.equal(context.portfolioOverviewInFlight, currentEntry, 'older finally handlers retain the current promise')
  requests.at(-1).reject(new Error('network unavailable'))
  assert.equal(await current, false)
  assert.equal(context.portfolioOverviewInFlight, null, 'failure releases the current in-flight entry')
  const retry = app.fetchPortfolioOverview()
  assert.notEqual(retry, current)
  requests.at(-1).resolve({ contract_version: 'portfolio-overview-v1' })
  assert.equal(await retry, true)
  assert.equal(context.portfolioOverviewInFlight, null)
})
