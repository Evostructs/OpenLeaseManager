import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { parse as parseJavaScript } from '@babel/parser'
import { parse } from '@vue/compiler-sfc'
import { computed, ref, shallowRef } from 'vue'
import { createPlanningDraftStore } from '../src/lib/planningDrafts.js'
import { createPlanningLeaveGuard, confirmPlanningLeaveWithoutEditor } from '../src/lib/planningLeaveGuard.js'
import { createBlankAssetStrategy } from '../src/lib/decisionScenarioDomain.js'
import {
  applyFinancialForecast, createPlanningScenario, expenseDraft, expenseHasChanges, expenseRequest,
  financialForecastChanged, versionBudgetReference
} from '../src/lib/financialPlanningDomain.js'

// Exercise the component's real load/refresh and version hydration functions.
// Transport is substituted; forecast validation, draft and version logic are real.
const { descriptor } = parse(readFileSync(new URL('../src/components/DecisionWorkbench.vue', import.meta.url), 'utf8'))
const script = descriptor.scriptSetup.content
const nodes = parseJavaScript(script, { sourceType: 'module' }).program.body
const functions = [
  'blankState', 'showError', 'previewPayload', 'applyApproved', 'setContractRows', 'useForecast',
  'hydrateVersion', 'alignBudgetToVersion', 'restoreClientControls', 'updateComparison', 'updateLiveForecast',
  'loadWorkspace', 'refreshData', 'calculateContractChanges', 'useActiveBudget',
  'saveExpense', 'adoptVersion', 'captureDraft', 'parkDraft', 'removeStoredDraft', 'restoreStoredDraft', 'requestLeave', 'changeYear', 'restoreVersion'
].map(name => {
  const node = nodes.find(item => item.type === 'FunctionDeclaration' && item.id.name === name)
  assert.ok(node, `DecisionWorkbench function ${name} is available`)
  return script.slice(node.start, node.end)
}).join('\n')
const clone = value => JSON.parse(JSON.stringify(value))

function snapshot(revenue = 10, cutoff = 8) {
  const months = Array.from({ length: 12 }, (_, index) => ({ month: index + 1, revenue, operating_cost: 2 }))
  return { year: 2026, unit: '万元', closed_through_month: cutoff, months,
    sources: [{ source_key: 'contract:1', source_type: 'contract', contract_id: 1, source_id: 1, months: clone(months) }] }
}

function version(id, kind, revenue = 10, budgetId = null) {
  return { id, kind, year: 2026, name: `Version ${id}`, closed_through_month: 8,
    budget_version_id: budgetId, snapshot: snapshot(revenue), created_at: '2026-09-11T08:00:00+08:00',
    client_state: { scenario: { annualTarget: revenue * 12 }, strategy: { mode: 'hold' },
      contractAdjustments: [{ contract_id: 1, start_shift_months: 2, rent_change_percent: 10 }] } }
}

function harness({ approved = null, latest = null, live = snapshot(15), history = [] } = {}) {
  const calls = []
  const records = new Map([approved, latest, ...history].filter(Boolean).map(item => [item.id, item]))
  const expense = { id: 1, name: '运营费用', amount_yuan: 20000, revision: 1, category: 'opex',
    occurrence_month: '2026-01', payment_month: '2026-02', is_active: true }
  const context = {
    clone, applyFinancialForecast, createPlanningScenario, createBlankAssetStrategy, expenseDraft,
    expenseHasChanges, expenseRequest, financialForecastChanged, versionBudgetReference, confirmPlanningLeaveWithoutEditor,
    props: { active: true, buildingCode: 'A' }, contextEpoch: 0, operationSequence: 0, workspacePending: false, suppressDirty: false,
    workspaceForecastReference: null, parkedRevision: ref(-1), editRevision: ref(0),
    planningDrafts: createPlanningDraftStore(), draftOwner: ref('account-A'), writable: ref(true),
    readable: ref(true), loading: ref(false), saving: ref(false), selectedYear: ref(2026), selectedCutoff: ref(0),
    sourceForecast: shallowRef(null), snapshotVersionId: ref(null), comparison: shallowRef(null),
    versions: shallowRef([]), expenses: ref([]), contractRows: ref([]), approvedVersion: shallowRef(null),
    activeBudgetVersion: shallowRef(null), selectedVersionId: ref(null), baseVersionId: ref(null),
    activeForecastId: ref(null), comparisonIds: ref([]), planName: ref(''), savedAt: ref(''), dirty: ref(false),
    message: ref(''), errorMessage: ref(''), validationRequested: ref(false), serverCanWrite: ref(false),
    serverRevision: ref(0), activeTab: ref('budget'), tableMode: ref('budget'),
    state: ref({ scenario: createPlanningScenario(2026), approved: null }),
    client: shallowRef({
      async source(year, cutoff, options) {
        calls.push({ method: 'source', year, cutoff, options })
        const latestResponse = latest && options.includeLatestSnapshot === false
          ? Object.fromEntries(Object.entries(latest).filter(([key]) => !['snapshot', 'client_state', 'contract_adjustments'].includes(key))) : latest
        return clone({ revision: 5, can_write: true, versions: [...records.values()], expenses: [expense],
          approved_budget: approved, latest_forecast: latestResponse,
          forecast: options.forecastMode === 'none' || latest || (options.forecastMode === 'workspace_deferred' && approved) ? null : live })
      },
      async version(id) { calls.push({ method: 'version', id }); return clone(records.get(id)) },
      async preview(payload) { calls.push({ method: 'preview', payload: clone(payload) }); return clone(live) },
      async compare(payload, options) {
        calls.push({ method: 'compare', payload: clone(payload), options })
        return { current: clone(payload.current_version_id ? records.get(payload.current_version_id).snapshot : live),
          comparison: { delta: 12, items: [{ source_key: 'contract:1', delta: 12 }] } }
      }
    })
  }
  context.scenario = computed(() => context.state.value.scenario)
  context.needsMeasureConfirmation = computed(() => (context.scenario.value.newLease.enabled || context.scenario.value.costSaving.enabled)
    && !context.scenario.value.baselineExcludesMeasures)
  context.adjustments = computed(() => context.contractRows.value
    .filter(row => Number(row.start_shift_months) !== 0 || Number(row.rent_change_percent) !== 0)
    .map(row => ({ contract_id: row.contract_id, start_shift_months: Number(row.start_shift_months), rent_change_percent: Number(row.rent_change_percent) })))
  const workbench = new Function('context', `with (context) { ${functions};
    return { saveExpense, adoptVersion, loadWorkspace, refreshData, calculateContractChanges, updateComparison, hydrateVersion, applyApproved, useActiveBudget, captureDraft, parkDraft, removeStoredDraft, restoreStoredDraft, requestLeave, changeYear, restoreVersion };
  }`)(context)
  context.planningDrafts.identify(context.draftOwner.value)
  context.draftContext = computed(() => JSON.stringify([context.draftOwner.value, context.props.buildingCode, context.selectedYear.value]))
  context.leaveGuard = createPlanningLeaveGuard({
    changed: () => (context.dirty.value || context.expenses.value.some(expenseHasChanges)) && context.editRevision.value !== context.parkedRevision.value,
    busy: () => context.loading.value || context.saving.value,
    save: () => workbench.parkDraft(),
    discard: async () => { workbench.removeStoredDraft(); await workbench.loadWorkspace({ restoreDraft: false }) }
  })
  return { workbench, context, calls, expense, live }
}

test('a workspace without a saved forecast uses the source fallback and produces a complete budget', async () => {
  const { workbench, context, calls, live } = harness()
  assert.equal(await workbench.loadWorkspace(), true)
  assert.deepEqual(calls.map(call => call.method), ['source'])
  assert.deepEqual(calls[0].options, { forecastMode: 'workspace_deferred' })
  assert.deepEqual(context.sourceForecast.value, live)
  assert.equal(context.scenario.value.budget.length, 12)
  assert.equal(context.scenario.value.budget[0].revenue, 15)
  assert.equal(context.state.value.editingBudget, true)
  assert.equal(context.dirty.value, false)
  assert.equal(context.loading.value, false)
})

test('saved workspace keeps its historical budget and remains loading until comparison is ready', async () => {
  const oldBudget = version(1, 'budget', 10)
  const latest = version(2, 'forecast', 12, 1)
  const currentBudget = version(3, 'budget', 20)
  const { workbench, context, calls } = harness({ approved: currentBudget, latest, history: [oldBudget] })
  let finish
  const compare = context.client.value.compare
  context.client.value.compare = async (...args) => {
    const result = await compare(...args)
    return new Promise(resolve => { finish = () => resolve(result) })
  }
  const pending = workbench.loadWorkspace()
  for (let index = 0; index < 10 && !finish; index += 1) await Promise.resolve()
  assert.ok(finish)
  assert.equal(context.loading.value, true)
  assert.equal(context.comparison.value, null)
  finish()
  assert.equal(await pending, true)
  assert.deepEqual(calls.map(call => call.method), ['source', 'version', 'compare'])
  assert.deepEqual(calls[0].options, { forecastMode: 'workspace_deferred' })
  assert.deepEqual(calls[2].options, { includeBaseline: false })
  assert.equal(calls[2].payload.baseline_version_id, oldBudget.id)
  assert.equal(calls[2].payload.current_version_id, latest.id)
  assert.equal(calls[2].payload.base_version_id, latest.id)
  assert.equal(context.approvedVersion.value.id, oldBudget.id)
  assert.equal(context.activeBudgetVersion.value.id, currentBudget.id)
  assert.equal(context.snapshotVersionId.value, latest.id)
  assert.equal(context.selectedCutoff.value, latest.closed_through_month)
  assert.deepEqual(context.sourceForecast.value, latest.snapshot)
  assert.deepEqual(context.adjustments.value, latest.client_state.contractAdjustments)
  assert.equal(context.state.value.strategy.mode, 'hold')
  assert.equal(context.comparison.value.delta, 12)
  assert.equal(context.loading.value, false)
  assert.equal(context.dirty.value, false)
})

test('refresh requests metadata only and preserves drafts, old budget binding, cutoff and adjustments', async () => {
  const oldBudget = version(1, 'budget', 10)
  const latest = version(2, 'forecast', 12, 1)
  const currentBudget = version(3, 'budget', 20)
  const { workbench, context, calls, expense, live } = harness({ approved: currentBudget, latest, history: [oldBudget] })
  workbench.applyApproved(oldBudget)
  workbench.hydrateVersion(latest)
  const existingDraft = expenseDraft(expense, 2026)
  existingDraft.amount = 7
  const unsaved = expenseDraft({ name: '草稿费用', amount_yuan: 50000 }, 2026)
  context.expenses.value = [existingDraft, unsaved]
  assert.equal(await workbench.refreshData({ preserveExpenseDrafts: true }), true)
  assert.deepEqual(calls.map(call => call.method), ['source', 'compare'])
  assert.deepEqual(calls[0].options, { forecastMode: 'none', includeLatestSnapshot: false })
  assert.deepEqual(calls[1].options, { includeBaseline: false })
  assert.equal(calls[1].payload.baseline_version_id, oldBudget.id)
  assert.equal('current_version_id' in calls[1].payload, false)
  assert.equal(calls[1].payload.base_version_id, latest.id)
  assert.equal(calls[1].payload.closed_through_month, 8)
  assert.deepEqual(calls[1].payload.contract_adjustments, latest.client_state.contractAdjustments)
  assert.deepEqual(context.sourceForecast.value, live)
  assert.equal(context.comparison.value.delta, 12)
  assert.equal(context.snapshotVersionId.value, null)
  assert.equal(context.selectedVersionId.value, latest.id)
  assert.equal(context.baseVersionId.value, latest.id)
  assert.equal(context.savedAt.value, latest.created_at)
  assert.equal(context.approvedVersion.value.id, oldBudget.id)
  assert.equal(context.activeBudgetVersion.value.id, currentBudget.id)
  assert.deepEqual(context.activeBudgetVersion.value.snapshot, currentBudget.snapshot)
  assert.equal(context.activeForecastId.value, latest.id)
  assert.deepEqual(context.expenses.value, clone([existingDraft, unsaved]))
  assert.equal(context.dirty.value, true)
  assert.equal(context.loading.value, false)
  calls.length = 0
  const changingBudget = workbench.useActiveBudget()
  await context.leaveGuard.choose('save')
  await changingBudget
  assert.deepEqual(calls.map(call => call.method), ['compare'])
  assert.equal(calls[0].payload.baseline_version_id, currentBudget.id)
  assert.equal(context.approvedVersion.value.id, currentBudget.id)
  assert.equal(context.scenario.value.budget[0].revenue, 20)
})

test('a budget-only workspace waits for one live comparison and keeps the approved budget receipt', async () => {
  const approved = version(1, 'budget', 10)
  const live = snapshot(15, 3)
  const { workbench, context, calls } = harness({ approved, live })
  context.selectedCutoff.value = 3
  const gate = deferred()
  const compare = context.client.value.compare
  context.client.value.compare = async (...args) => {
    const result = await compare(...args)
    await gate.promise
    return result
  }
  const pending = workbench.loadWorkspace()
  await untilStarted(() => calls.some(call => call.method === 'compare'))
  assert.equal(context.loading.value, true)
  assert.equal(context.comparison.value, null)
  assert.equal(context.savedAt.value, approved.created_at)
  assert.equal(context.activeTab.value, 'measures')
  assert.equal(context.state.value.editingBudget, false)
  gate.resolve()
  assert.equal(await pending, true)
  assert.deepEqual(calls.map(call => call.method), ['source', 'compare'])
  assert.deepEqual(calls[0].options, { forecastMode: 'workspace_deferred' })
  assert.deepEqual(calls[1].payload, { year: 2026, closed_through_month: 3, base_version_id: null,
    contract_adjustments: [], baseline_version_id: approved.id })
  assert.deepEqual(calls[1].options, { includeBaseline: false })
  assert.deepEqual(context.sourceForecast.value, live)
  assert.equal(context.scenario.value.budget[0].revenue, 10)
  assert.equal(context.scenario.value.forecast[3].revenue, 15)
  assert.equal(context.approvedVersion.value.id, approved.id)
  assert.equal(context.baseVersionId.value, null)
  assert.equal(context.snapshotVersionId.value, null)
  assert.equal(context.selectedVersionId.value, null)
  assert.equal(context.comparison.value.delta, 12)
  assert.equal(context.dirty.value, false)
  assert.equal(context.message.value, '')
  assert.equal(context.errorMessage.value, '')
  assert.equal(context.loading.value, false)
})

for (const previewFails of [false, true]) {
  test(`a budget-only workspace preserves its receipt and clean state when comparison fails and preview ${previewFails ? 'fails' : 'succeeds'}`, async () => {
    const approved = version(1, 'budget', 10)
    approved.client_state.scenario.newLease = { enabled: true, monthlyRent: 5 }
    approved.client_state.scenario.baselineExcludesMeasures = true
    const { workbench, context, calls, live } = harness({ approved })
    const compare = context.client.value.compare
    const preview = context.client.value.preview
    context.client.value.compare = async (...args) => { await compare(...args); throw new Error('比较读取失败') }
    context.client.value.preview = async (...args) => {
      const result = await preview(...args)
      if (previewFails) throw new Error('实时预测读取失败')
      return result
    }
    assert.equal(await workbench.loadWorkspace(), false)
    assert.deepEqual(calls.map(call => call.method), ['source', 'compare', 'preview'])
    const { baseline_version_id, ...payload } = calls[1].payload
    assert.equal(baseline_version_id, approved.id)
    assert.deepEqual(calls[2].payload, payload)
    assert.deepEqual(context.sourceForecast.value, previewFails ? null : live)
    assert.deepEqual(context.workspaceForecastReference, previewFails ? approved.snapshot : null)
    assert.equal(context.contractRows.value.length, previewFails ? 0 : live.sources.length)
    assert.deepEqual(context.approvedVersion.value, approved)
    assert.deepEqual(context.activeBudgetVersion.value, approved)
    assert.equal(context.scenario.value.newLease.enabled, true)
    assert.equal(context.scenario.value.baselineExcludesMeasures, previewFails)
    assert.equal(context.scenario.value.forecast[8].revenue, previewFails ? null : 15)
    assert.equal(context.scenario.value.budget[0].revenue, 10)
    assert.equal(context.comparison.value, null)
    assert.equal(context.snapshotVersionId.value, null)
    assert.equal(context.baseVersionId.value, null)
    assert.equal(context.savedAt.value, approved.created_at)
    assert.equal(context.activeTab.value, 'measures')
    assert.equal(context.state.value.editingBudget, false)
    assert.equal(context.dirty.value, false)
    assert.equal(context.loading.value, false)
    assert.equal(context.errorMessage.value, previewFails ? '实时预测读取失败' : '比较读取失败')
    assert.equal(context.message.value, previewFails ? '' : '业务基准已变化，请核对新增租约和降本是否已计入。')
  })
}

for (const changed of [false, true]) {
  test(`retrying a failed budget-only workspace ${changed ? 'requires' : 'preserves'} measure confirmation when business sources ${changed ? 'change' : 'stay the same'}`, async () => {
    const approved = version(1, 'budget', 10)
    approved.client_state.scenario.newLease = { enabled: true, monthlyRent: 5 }
    approved.client_state.scenario.baselineExcludesMeasures = true
    const live = snapshot(changed ? 15 : 10)
    const { workbench, context, calls } = harness({ approved, live })
    context.selectedCutoff.value = 8
    const compare = context.client.value.compare
    const preview = context.client.value.preview
    context.client.value.compare = async (...args) => { await compare(...args); throw new Error('比较读取失败') }
    context.client.value.preview = async (...args) => { await preview(...args); throw new Error('实时预测读取失败') }
    assert.equal(await workbench.loadWorkspace(), false)
    assert.equal(context.sourceForecast.value, null)
    assert.deepEqual(context.workspaceForecastReference, approved.snapshot)
    assert.equal(context.scenario.value.baselineExcludesMeasures, true)
    context.client.value.compare = compare
    context.client.value.preview = preview
    calls.length = 0
    assert.equal(await workbench.refreshData(), true)
    assert.deepEqual(calls.map(call => call.method), ['source', 'compare'])
    assert.deepEqual(context.sourceForecast.value, live)
    assert.equal(context.workspaceForecastReference, null)
    assert.equal(context.scenario.value.baselineExcludesMeasures, !changed)
    assert.equal(context.needsMeasureConfirmation.value, changed)
    assert.equal(context.savedAt.value, approved.created_at)
    assert.deepEqual(context.approvedVersion.value, approved)
    assert.equal(context.dirty.value, true)
    assert.equal(context.errorMessage.value, '')
    assert.equal(context.message.value, changed ? '业务基准已变化，请核对新增租约和降本是否已计入。'
      : '合同和费用预测已更新。保存并采用后，下次会载入这份预测。')
  })
}

for (const nextContext of ['hidden', 'unreadable', 'source failure']) {
  test(`a new ${nextContext} workspace discards the prior failed workspace comparison reference`, async () => {
    const approved = version(1, 'budget')
    const { workbench, context } = harness({ approved })
    context.client.value.compare = async () => { throw new Error('比较读取失败') }
    context.client.value.preview = async () => { throw new Error('实时预测读取失败') }
    assert.equal(await workbench.loadWorkspace(), false)
    assert.deepEqual(context.workspaceForecastReference, approved.snapshot)
    if (nextContext === 'hidden') context.props.active = false
    else if (nextContext === 'unreadable') context.readable.value = false
    else context.client.value.source = async () => { throw new Error('新项目读取失败') }
    await workbench.loadWorkspace()
    assert.equal(context.workspaceForecastReference, null)
    assert.equal(context.sourceForecast.value, null)
    assert.equal(context.contractRows.value.length, 0)
  })
}

test('loading a saved version discards an unloaded workspace comparison reference', () => {
  const { workbench, context } = harness()
  context.workspaceForecastReference = snapshot(10)
  const saved = version(2, 'forecast', 20)
  workbench.hydrateVersion(saved)
  assert.equal(context.workspaceForecastReference, null)
  assert.deepEqual(context.sourceForecast.value, saved.snapshot)
  assert.equal(context.snapshotVersionId.value, saved.id)
})

for (const staleKey of ['contextEpoch', 'operationSequence']) {
  for (const compareFails of [false, true]) {
    test(`a budget-only workspace ignores a ${compareFails ? 'failed' : 'successful'} comparison after ${staleKey} changes`, async () => {
      const { workbench, context, calls } = harness({ approved: version(1, 'budget') })
      const gate = deferred()
      const compare = context.client.value.compare
      context.client.value.compare = (...args) => { void compare(...args); return gate.promise }
      const pending = workbench.loadWorkspace()
      await untilStarted(() => calls.some(call => call.method === 'compare'))
      context[staleKey] += 1
      const replacement = snapshot(99)
      const replacementComparison = { delta: 999 }
      const replacementReference = snapshot(77)
      context.sourceForecast.value = replacement
      context.comparison.value = replacementComparison
      context.workspaceForecastReference = replacementReference
      context.savedAt.value = '新页面保存时间'
      context.message.value = '新页面回执'
      context.errorMessage.value = '新页面状态'
      context.dirty.value = true
      context.loading.value = false
      if (compareFails) gate.reject(new Error('旧比较读取失败'))
      else gate.resolve({ current: snapshot(20), comparison: { delta: 120 } })
      await pending
      assert.deepEqual(calls.map(call => call.method), ['source', 'compare'])
      assert.equal(context.sourceForecast.value, replacement)
      assert.equal(context.comparison.value, replacementComparison)
      assert.equal(context.workspaceForecastReference, replacementReference)
      assert.equal(context.savedAt.value, '新页面保存时间')
      assert.equal(context.message.value, '新页面回执')
      assert.equal(context.errorMessage.value, '新页面状态')
      assert.equal(context.dirty.value, true)
      assert.equal(context.loading.value, false)
    })
  }

  test(`a budget-only workspace ignores fallback preview after ${staleKey} changes`, async () => {
    const { workbench, context, calls } = harness({ approved: version(1, 'budget') })
    const gate = deferred()
    const compare = context.client.value.compare
    const preview = context.client.value.preview
    context.client.value.compare = async (...args) => { await compare(...args); throw new Error('比较读取失败') }
    context.client.value.preview = (...args) => { void preview(...args); return gate.promise }
    const pending = workbench.loadWorkspace()
    await untilStarted(() => calls.some(call => call.method === 'preview'))
    context[staleKey] += 1
    const replacement = snapshot(99)
    const replacementReference = snapshot(77)
    context.sourceForecast.value = replacement
    context.workspaceForecastReference = replacementReference
    context.snapshotVersionId.value = 99
    context.savedAt.value = '新页面保存时间'
    context.errorMessage.value = '新页面状态'
    context.loading.value = false
    gate.resolve(snapshot(20))
    await pending
    assert.deepEqual(calls.map(call => call.method), ['source', 'compare', 'preview'])
    assert.equal(context.sourceForecast.value, replacement)
    assert.equal(context.workspaceForecastReference, replacementReference)
    assert.equal(context.snapshotVersionId.value, 99)
    assert.equal(context.savedAt.value, '新页面保存时间')
    assert.equal(context.errorMessage.value, '新页面状态')
    assert.equal(context.dirty.value, false)
    assert.equal(context.loading.value, false)
  })
}

const liveActions = [
  { name: 'refresh', method: 'refreshData', sourceCalls: ['source'], success: true, failure: false },
  { name: 'contract calculation', method: 'calculateContractChanges', sourceCalls: [], success: undefined, failure: undefined }
]

function deferred() {
  let resolve
  let reject
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail })
  return { promise, resolve, reject }
}

async function untilStarted(predicate) {
  for (let index = 0; index < 10 && !predicate(); index += 1) await Promise.resolve()
  assert.ok(predicate(), 'request has started')
}

for (const action of liveActions) {
  test(`${action.name} without a budget uses one preview and clears a saved snapshot only on success`, async () => {
    const { workbench, context, calls, live } = harness()
    workbench.hydrateVersion(version(2, 'scenario', 12))
    context.comparison.value = { delta: -12 }
    assert.equal(await workbench[action.method](), action.success)
    assert.deepEqual(calls.map(call => call.method), [...action.sourceCalls, 'preview'])
    const request = calls.at(-1)
    assert.equal(request.payload.base_version_id, 2)
    assert.equal(request.payload.closed_through_month, 8)
    assert.deepEqual(request.payload.contract_adjustments, [{ contract_id: 1, start_shift_months: 2, rent_change_percent: 10 }])
    assert.deepEqual(context.sourceForecast.value, live)
    assert.equal(context.snapshotVersionId.value, null)
    assert.equal(context.comparison.value, null)
    assert.equal(context.dirty.value, true)
    assert.equal(context.loading.value, false)
  })

  test(`${action.name} with a historical budget calculates current data once and retains the saved binding`, async () => {
    const oldBudget = version(1, 'budget', 10)
    const latest = version(2, 'forecast', 12, 1)
    const currentBudget = version(3, 'budget', 20)
    const { workbench, context, calls, live } = harness({ approved: currentBudget, latest, history: [oldBudget] })
    workbench.applyApproved(oldBudget)
    workbench.hydrateVersion(latest)
    assert.equal(await workbench[action.method](), action.success)
    assert.deepEqual(calls.map(call => call.method), [...action.sourceCalls, 'compare'])
    assert.deepEqual(calls.at(-1).options, { includeBaseline: false })
    assert.deepEqual(calls.at(-1).payload, { year: 2026, closed_through_month: 8, base_version_id: latest.id,
      contract_adjustments: latest.client_state.contractAdjustments, baseline_version_id: oldBudget.id })
    assert.deepEqual(context.sourceForecast.value, live)
    assert.equal(context.comparison.value.delta, 12)
    assert.equal(context.snapshotVersionId.value, null)
    assert.equal(context.approvedVersion.value.id, oldBudget.id)
    assert.equal(context.selectedVersionId.value, latest.id)
    assert.equal(context.baseVersionId.value, latest.id)
    assert.equal(context.savedAt.value, latest.created_at)
    assert.deepEqual(context.adjustments.value, latest.client_state.contractAdjustments)
    assert.equal(context.dirty.value, true)
    assert.equal(context.loading.value, false)
  })

  for (const previousDirty of [false, true]) {
    test(`${action.name} falls back after comparison failure and preserves dirty=${previousDirty} and the receipt`, async () => {
      const { workbench, context, calls, live } = harness({ approved: version(1, 'budget') })
      workbench.applyApproved(version(1, 'budget'))
      workbench.hydrateVersion(version(2, 'forecast', 12, 1))
      context.comparison.value = { delta: -12 }
      context.dirty.value = previousDirty
      context.message.value = '此前成功回执'
      const compare = context.client.value.compare
      context.client.value.compare = async (...args) => {
        await compare(...args)
        throw new Error('比较读取失败')
      }
      assert.equal(await workbench[action.method](), action.failure)
      assert.deepEqual(calls.map(call => call.method), [...action.sourceCalls, 'compare', 'preview'])
      const { baseline_version_id, ...livePayload } = calls.at(-2).payload
      assert.equal(baseline_version_id, 1)
      assert.deepEqual(calls.at(-1).payload, livePayload)
      assert.deepEqual(context.sourceForecast.value, live)
      assert.equal(context.snapshotVersionId.value, null)
      assert.equal(context.comparison.value, null)
      assert.equal(context.errorMessage.value, '比较读取失败')
      assert.equal(context.message.value, '此前成功回执')
      assert.equal(context.savedAt.value, '2026-09-11T08:00:00+08:00')
      assert.equal(context.dirty.value, previousDirty)
      assert.equal(context.loading.value, false)
    })
  }

  test(`${action.name} keeps the previous forecast and comparison if both requests fail`, async () => {
    const { workbench, context, calls } = harness({ approved: version(1, 'budget') })
    workbench.applyApproved(version(1, 'budget'))
    workbench.hydrateVersion(version(2, 'forecast', 12, 1))
    const originalForecast = context.sourceForecast.value
    const originalComparison = { delta: -12 }
    context.comparison.value = originalComparison
    context.message.value = '此前成功回执'
    const compare = context.client.value.compare
    const preview = context.client.value.preview
    context.client.value.compare = async (...args) => { await compare(...args); throw new Error('比较读取失败') }
    context.client.value.preview = async (...args) => { await preview(...args); throw new Error('实时预测读取失败') }
    assert.equal(await workbench[action.method](), action.failure)
    assert.deepEqual(calls.map(call => call.method), [...action.sourceCalls, 'compare', 'preview'])
    assert.equal(context.sourceForecast.value, originalForecast)
    assert.equal(context.comparison.value, originalComparison)
    assert.equal(context.snapshotVersionId.value, 2)
    assert.equal(context.errorMessage.value, '实时预测读取失败')
    assert.equal(context.message.value, '此前成功回执')
    assert.equal(context.dirty.value, false)
    assert.equal(context.loading.value, false)
  })

  for (const staleKey of ['contextEpoch', 'operationSequence']) {
    for (const compareFails of [false, true]) {
      test(`${action.name} ignores ${compareFails ? 'failed' : 'successful'} comparison after ${staleKey} changes`, async () => {
        const { workbench, context, calls } = harness({ approved: version(1, 'budget') })
        workbench.applyApproved(version(1, 'budget'))
        const gate = deferred()
        const compare = context.client.value.compare
        context.client.value.compare = (...args) => { void compare(...args); return gate.promise }
        const pending = workbench[action.method]()
        await untilStarted(() => calls.some(call => call.method === 'compare'))
        context[staleKey] += 1
        const replacement = snapshot(99)
        const replacementComparison = { delta: 999 }
        context.sourceForecast.value = replacement
        context.comparison.value = replacementComparison
        context.message.value = '新页面回执'
        context.errorMessage.value = '新页面状态'
        context.loading.value = false
        if (compareFails) gate.reject(new Error('旧比较读取失败'))
        else gate.resolve({ current: snapshot(20), comparison: { delta: 120 } })
        await pending
        assert.deepEqual(calls.map(call => call.method), [...action.sourceCalls, 'compare'])
        assert.equal(context.sourceForecast.value, replacement)
        assert.equal(context.comparison.value, replacementComparison)
        assert.equal(context.message.value, '新页面回执')
        assert.equal(context.errorMessage.value, '新页面状态')
        assert.equal(context.dirty.value, false)
        assert.equal(context.loading.value, false)
      })
    }

    test(`${action.name} ignores fallback preview after ${staleKey} changes`, async () => {
      const { workbench, context, calls } = harness({ approved: version(1, 'budget') })
      workbench.applyApproved(version(1, 'budget'))
      const gate = deferred()
      const compare = context.client.value.compare
      const preview = context.client.value.preview
      context.client.value.compare = async (...args) => { await compare(...args); throw new Error('比较读取失败') }
      context.client.value.preview = (...args) => { void preview(...args); return gate.promise }
      const pending = workbench[action.method]()
      await untilStarted(() => calls.some(call => call.method === 'preview'))
      context[staleKey] += 1
      const replacement = snapshot(99)
      const replacementComparison = { delta: 999 }
      context.sourceForecast.value = replacement
      context.comparison.value = replacementComparison
      context.snapshotVersionId.value = 99
      context.errorMessage.value = '新页面状态'
      context.loading.value = false
      gate.resolve(snapshot(20))
      await pending
      assert.equal(context.sourceForecast.value, replacement)
      assert.equal(context.comparison.value, replacementComparison)
      assert.equal(context.snapshotVersionId.value, 99)
      assert.equal(context.errorMessage.value, '新页面状态')
      assert.equal(context.dirty.value, false)
      assert.equal(context.loading.value, false)
    })
  }
}

test('a comparison that finishes after the project context changes cannot replace the forecast', async () => {
  const { workbench, context } = harness()
  workbench.applyApproved(version(1, 'budget'))
  context.sourceForecast.value = snapshot(10)
  let finish
  context.client.value.compare = async () => new Promise(resolve => { finish = resolve })
  const pending = workbench.updateComparison()
  context.contextEpoch += 1
  const replacement = snapshot(99)
  context.sourceForecast.value = replacement
  finish({ current: snapshot(20), comparison: { delta: 120 } })
  await pending
  assert.deepEqual(context.sourceForecast.value, replacement)
  assert.equal(context.comparison.value, null)
})


test('cancelled year and history changes keep the complete working draft and make no request', async () => {
  const { workbench, context, calls } = harness({ history: [version(9, 'budget')] })
  await workbench.loadWorkspace()
  context.planName.value = 'unsaved name'
  context.scenario.value.annualTarget = 321
  context.expenses.value[0].amount = 12
  context.contractRows.value[0].rent_change_percent = 15
  context.dirty.value = true
  const before = workbench.captureDraft()
  calls.length = 0
  const event = { target: { value: '2027' } }
  const changing = workbench.changeYear(event)
  assert.equal(event.target.value, '2026')
  assert.equal(context.selectedYear.value, 2026)
  assert.equal(context.leaveGuard.state.open, true)
  await context.leaveGuard.choose('stay')
  await changing
  const history = workbench.restoreVersion(9)
  await context.leaveGuard.choose('stay')
  await history
  assert.deepEqual(workbench.captureDraft(), before)
  assert.deepEqual(calls, [])
})

test('park then change year restores all inputs on return without any financial write', async () => {
  const { workbench, context, calls } = harness()
  await workbench.loadWorkspace()
  context.planName.value = 'parked plan'
  context.scenario.value.annualTarget = 222
  context.expenses.value[0].amount = 42
  context.contractRows.value[0].start_shift_months = 2
  context.dirty.value = true
  const before = workbench.captureDraft()
  const changing = workbench.changeYear({ target: { value: '2027' } })
  await context.leaveGuard.choose('save')
  await changing
  assert.equal(context.selectedYear.value, 2027)
  assert.notEqual(context.planName.value, 'parked plan')
  await workbench.changeYear({ target: { value: '2026' } })
  assert.deepEqual(workbench.captureDraft(), before)
  assert.ok(calls.every(call => ['source', 'preview', 'compare', 'version'].includes(call.method)))
  assert.match(context.message.value, /已恢复暂存草稿/)
})

test('explicitly discarded edits are not restored, and refresh alone preserves expense drafts', async () => {
  const { workbench, context } = harness()
  await workbench.loadWorkspace()
  context.planName.value = 'discard me'
  context.expenses.value[0].amount = 73
  context.dirty.value = true
  await workbench.refreshData()
  assert.equal(context.expenses.value[0].amount, 73)
  const changing = workbench.changeYear({ target: { value: '2027' } })
  await context.leaveGuard.choose('discard')
  await changing
  await workbench.changeYear({ target: { value: '2026' } })
  assert.equal(context.planName.value, '')
  assert.equal(context.expenses.value[0].amount, 2)
})

test('revoked read access clears visible fields and cannot restore a retained draft', async () => {
  const { workbench, context } = harness()
  await workbench.loadWorkspace()
  context.planName.value = 'private draft'
  context.dirty.value = true
  workbench.parkDraft()
  context.readable.value = false
  await workbench.loadWorkspace()
  assert.equal(context.planName.value, '')
  assert.deepEqual(context.expenses.value, [])
  assert.equal(workbench.restoreStoredDraft(), false)
})


test('a refreshed forecast after parking becomes unparked again', async () => {
  const { workbench, context } = harness()
  await workbench.loadWorkspace()
  context.dirty.value = true
  workbench.parkDraft()
  assert.equal(await workbench.requestLeave('temporarily leave'), true)
  await workbench.calculateContractChanges()
  const leaving = workbench.requestLeave('exit')
  assert.equal(context.leaveGuard.state.open, true)
  await context.leaveGuard.choose('stay')
  assert.equal(await leaving, false)
})

test('a confirmed expense write invalidates an older parked new-row copy even if refresh fails', async () => {
  const { workbench, context } = harness()
  await workbench.loadWorkspace()
  const row = expenseDraft({ name: 'new expense', amount_yuan: 12000 }, 2026)
  context.expenses.value.push(row)
  workbench.parkDraft()
  context.client.value.saveExpense = async () => ({ id: 20, name: 'new expense', amount_yuan: 12000, revision: 1 })
  context.client.value.source = async () => { throw new Error('refresh unavailable') }
  await workbench.saveExpense(row)
  assert.equal(row.id, 20)
  assert.equal(context.planningDrafts.has(context.draftOwner.value), false)
  assert.equal(context.dirty.value, true)
  assert.match(context.message.value, /已保存/)
  const leave = workbench.requestLeave('exit')
  assert.equal(context.leaveGuard.state.open, true)
  await context.leaveGuard.choose('stay')
  assert.equal(await leave, false)
})

test('adopting a saved version does not restore an unrelated parked draft over it', async () => {
  const { workbench, context } = harness()
  await workbench.loadWorkspace()
  context.planName.value = 'old parked draft'
  workbench.parkDraft()
  context.dirty.value = false
  context.hasExpenseChanges = computed(() => context.expenses.value.some(expenseHasChanges))
  context.client.value.adoptVersion = async () => ({ version: { name: 'chosen version' } })
  await workbench.adoptVersion(9)
  assert.equal(context.planningDrafts.has(context.draftOwner.value), false)
  assert.notEqual(context.planName.value, 'old parked draft')
  assert.match(context.message.value, /已采用/)
})
