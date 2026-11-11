import assert from 'node:assert/strict'
import test from 'node:test'
import { calculateBudgetScenario, createDemoScenario } from '../src/lib/decisionScenarioDomain.js'
import {
  applyFinancialForecast, buildPlanningBridge, buildSourcePlanningBase, buildSourcePlanningBridge, createPlanningScenario,
  currentPlanningInput, expenseDraft, expenseHasChanges, expenseRequest, financialForecastChanged,
  planningBridgeBars, planningDrillRows, versionBudgetReference
} from '../src/lib/financialPlanningDomain.js'

test('a frozen budget stays fixed while actuals, cutoff and forecast can roll forward', () => {
  const approved = createDemoScenario(2026)
  const current = structuredClone(approved)
  current.closedThroughMonth = 9
  current.actual[8] = { revenue: 111, operatingCost: 31 }
  current.forecast[9].revenue = 200
  current.budget[0].revenue = 999
  current.annualTarget = 9999
  const input = currentPlanningInput(current, approved)
  assert.equal(input.closedThroughMonth, 9)
  assert.deepEqual(input.actual[8], { revenue: 111, operatingCost: 31 })
  assert.equal(input.forecast[9].revenue, 200)
  assert.equal(input.budget[0].revenue, approved.budget[0].revenue)
  assert.equal(input.annualTarget, approved.annualTarget)
  assert.equal(approved.closedThroughMonth, 8)
})

test('variance bridge and every monthly drilldown reconcile to the current result', () => {
  const result = calculateBudgetScenario(createDemoScenario(2026))
  const bridge = buildPlanningBridge(result)
  assert.equal(bridge[0].value, result.annual.budgetEbitda)
  assert.equal(bridge[4].value, result.annual.baselineForecast)
  assert.ok(Math.abs(bridge.at(-1).value - result.annual.withMeasures) < 1e-8)
  for (const item of bridge) {
    assert.equal(item.value, item.details.reduce((sum, row) => sum + row.value, 0))
    if (!item.total) assert.ok(Math.abs(item.end - item.start - item.value) < 1e-8)
  }
  assert.ok(Math.abs(bridge[3].end - bridge[4].value) < 1e-8)
  assert.ok(Math.abs(bridge[7].end - bridge[8].value) < 1e-8)
  assert.ok(bridge.find(item => item.key === 'actual').details.every(row => row.month <= 8))
  assert.ok(bridge.find(item => item.key === 'revenue').details.every(row => row.month > 8))
})

test('zero, negative and zero-crossing bridge bars preserve their endpoints', () => {
  const cases = [
    { start: 0, end: 100 }, { start: 100, end: 80 }, { start: -100, end: -60 },
    { start: -60, end: -100 }, { start: -30, end: 20 }, { start: 20, end: -30 }, { start: 0, end: 0 }
  ]
  const bars = planningBridgeBars(cases)
  cases.forEach((item, index) => {
    const bar = bars[index]
    const top = Math.max(0, bar.offset) + bar.positive
    const bottom = Math.min(0, bar.offset) + bar.negative
    if (item.start >= 0 && item.end >= 0) assert.equal(top - bar.offset, Math.abs(item.end - item.start))
    else if (item.start <= 0 && item.end <= 0) assert.equal(bar.offset - bottom, Math.abs(item.end - item.start))
    else assert.equal(top - bottom, Math.abs(item.end - item.start))
  })
})

test('missing scenario inputs never create an apparently reconciled zero bridge', () => {
  assert.deepEqual(buildPlanningBridge({ valid: false }), [])
})

function forecast(cutoff = 8) {
  return { year: 2026, unit: '万元', closed_through_month: cutoff, months: Array.from({ length: 12 }, (_, index) => ({ month: index + 1, revenue: 20 + index, operating_cost: 5 })) }
}

test('forecast mapping requires explicit units and complete finite months without altering frozen budgets', () => {
  const input = createPlanningScenario(2026)
  const first = applyFinancialForecast(input, forecast(), { seedBudget: true })
  assert.equal(first.actual[7].revenue, 27)
  assert.equal(first.actual[8].revenue, null)
  assert.equal(first.forecast[7].revenue, null)
  assert.equal(first.forecast[8].revenue, 28)
  assert.equal(first.annualTarget, 246)
  first.budget[0].revenue = 123
  first.annualTarget = 500
  const updated = applyFinancialForecast(first, forecast(9))
  assert.equal(updated.budget[0].revenue, 123)
  assert.equal(updated.annualTarget, 500)
  assert.equal(first.actual[8].revenue, null)
  assert.equal(updated.actual[8].revenue, 28)
  for (const invalid of [
    { ...forecast(), unit: '元' },
    { ...forecast(), months: forecast().months.slice(1) },
    { ...forecast(), months: forecast().months.map(row => ({ ...row, month: 1 })) },
    { ...forecast(), months: forecast().months.map(row => ({ ...row, revenue: null })) },
    { ...forecast(), closed_through_month: 13 }
  ]) assert.throws(() => applyFinancialForecast(first, invalid))
})

test('expense editing preserves cents, revision and unpaid status and detects unsaved changes', () => {
  const draft = expenseDraft({ id: 8, name: '物业维修', category: 'opex', amount_yuan: 123456.78, occurrence_month: '2026-09', payment_month: '2026-10', paid_on: null, revision: 4 })
  assert.equal(draft.amount, 12.345678)
  assert.equal(expenseHasChanges(draft), false)
  const request = expenseRequest(draft)
  assert.equal(request.amount_yuan, 123456.78)
  assert.equal(request.paid_on, null)
  assert.equal(request.expected_revision, 4)
  draft.amount = 15
  assert.equal(expenseHasChanges(draft), true)
  assert.equal(expenseRequest(draft).amount_yuan, 150000)
  assert.throws(() => expenseRequest({ ...draft, amount: '' }))
  assert.throws(() => expenseRequest({ ...draft, name: ' ' }))
})

test('server source bridge keeps budget adjustments explicit and monthly drills add up', () => {
  const result = calculateBudgetScenario(createDemoScenario(2026))
  const item = (source_type, source_id, label, baseline_value, current_value) => ({
    source_type, source_id, source_key: `${source_type}:${source_id}`, label,
    baseline_value, current_value, delta: current_value - baseline_value,
    months: [{ month: 9, baseline_value, current_value, delta: current_value - baseline_value }]
  })
  const comparison = { baseline_total: 1050, current_total: 900, items: [
    item('contract', 7, '租约 A', 1100, 1000), item('expense', 8, '维修费', -100, -100),
    item('budget_adjustment', 1, '预算编制调整', 50, 0)
  ] }
  comparison.all_sources = comparison.items
  const bridge = buildSourcePlanningBridge(comparison, result)
  for (const row of bridge) {
    assert.ok(Math.abs(row.value - planningDrillRows(row).reduce((sum, detail) => sum + detail.value, 0)) < 1e-8)
  }
  assert.equal(bridge.find(row => row.key === 'contract').value, -100)
  assert.equal(bridge.find(row => row.key === 'budget_adjustment').value, -50)
  const sourceRow = planningDrillRows(bridge.find(row => row.key === 'contract'), 9)[0]
  assert.equal(sourceRow.contractId, 7)
  assert.equal(sourceRow.value, -100)
  assert.deepEqual(planningDrillRows(bridge.find(row => row.key === 'contract'), 1), [])
  assert.ok(Math.abs(bridge.at(-1).value - result.annual.withMeasures) < 1e-8)
})

test('total bridge columns include unchanged sources while difference columns contain only changes', () => {
  const input = createDemoScenario(2026)
  input.newLease.enabled = false
  input.costSaving.enabled = false
  const result = calculateBudgetScenario(input)
  const changed = { source_key: 'contract:1', source_type: 'contract', source_id: 1, label: '合同一', baseline_value: 100, current_value: 110, delta: 10, months: [{ month: 1, baseline_value: 100, current_value: 110, delta: 10 }] }
  const unchanged = { source_key: 'contract:2', source_type: 'contract', source_id: 2, label: '合同二', baseline_value: 200, current_value: 200, delta: 0, months: [{ month: 1, baseline_value: 200, current_value: 200, delta: 0 }] }
  const bridge = buildSourcePlanningBridge({ baseline_total: 300, current_total: 310, items: [changed], all_sources: [changed, unchanged] }, result)
  assert.equal(planningDrillRows(bridge[0]).reduce((sum, row) => sum + row.value, 0), 300)
  assert.equal(planningDrillRows(bridge.find(row => row.key === 'baseline')).reduce((sum, row) => sum + row.value, 0), 310)
  assert.equal(bridge.find(row => row.key === 'contract').details.length, 1)
  assert.equal(planningDrillRows(bridge.find(row => row.key === 'baseline'), 1).length, 2)
})

test('historical forecasts retain their own approved budget reference instead of the latest target', () => {
  const savedScenario = applyFinancialForecast(createPlanningScenario(2026), forecast(), { seedBudget: true })
  savedScenario.annualTarget = 1000
  const historical = { id: 5, year: 2026, budget_version_id: 1, client_state: { scenario: savedScenario } }
  const activeBudget = { id: 2, year: 2026, snapshot: forecast(), client_state: { scenario: { annualTarget: 1200 } } }
  assert.equal(versionBudgetReference(historical, activeBudget), 1)
  const withoutReference = { ...historical, budget_version_id: null }
  assert.equal(versionBudgetReference(withoutReference, activeBudget), null, 'a different current target cannot be inferred as the historical budget')
  activeBudget.client_state.scenario.annualTarget = 1000
  assert.equal(versionBudgetReference(withoutReference, activeBudget), 2)
  savedScenario.budget[0].revenue += 1
  assert.equal(versionBudgetReference(withoutReference, activeBudget), null)
})

test('business-source changes require rechecking measures but timestamp-only refreshes do not', () => {
  const original = { year: 2026, closed_through_month: 8, generated_at: 'first', sources: [
    { source_key: 'contract:1', source_type: 'contract', months: [{ month: 9, revenue: 10, operating_cost: 0 }] }
  ] }
  assert.equal(financialForecastChanged(original, { ...original, generated_at: 'later' }), false)
  const changed = structuredClone(original)
  changed.sources.push({ source_key: 'contract:2', source_type: 'contract', months: [{ month: 9, revenue: 30, operating_cost: 0 }] })
  assert.equal(financialForecastChanged(original, changed), true)
  const budget = structuredClone(original)
  budget.sources.push({ source_key: 'budget:1', source_type: 'budget_adjustment', months: [{ month: 9, revenue: 2, operating_cost: 0 }] })
  assert.equal(financialForecastChanged(original, budget), false)
})

test('measure updates reuse immutable contract bridge details without changing amounts or source drilldowns', () => {
  const sources = Array.from({ length: 200 }, (_, index) => ({
    source_key: `contract:${index}`, source_type: 'contract', source_id: index,
    baseline_value: 100, current_value: 110, delta: 10,
    months: [{ month: 9, baseline_value: 100, current_value: 110, delta: 10 }]
  }))
  const comparison = { baseline_total: 20000, current_total: 22000, items: sources, all_sources: sources }
  const base = buildSourcePlanningBase(comparison)
  const originalBase = structuredClone(base)
  const scenario = createDemoScenario(2026)
  for (let value = 1; value <= 24; value += 1) {
    scenario.newLease.monthlyRent = value
    const result = calculateBudgetScenario(scenario)
    const cached = buildSourcePlanningBridge(comparison, result, base)
    assert.deepEqual(cached, buildSourcePlanningBridge(comparison, result))
    assert.equal(cached.find(row => row.key === 'contract').details, base.find(row => row.key === 'contract').details)
    assert.equal(cached.at(-1).value, comparison.current_total + result.annual.ebitdaIncrement)
    assert.equal(planningDrillRows(cached.find(row => row.key === 'contract'), 9).length, 200)
  }
  assert.deepEqual(base, originalBase)
})
