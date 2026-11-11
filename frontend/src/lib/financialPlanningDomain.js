import { createBlankScenario } from './decisionScenarioDomain.js'

export const clonePlanningValue = (value) => JSON.parse(JSON.stringify(value))

// Approval freezes the target and monthly budget, not later actuals or forecasts.
export function currentPlanningInput(scenario, approved) {
  return {
    ...scenario,
    budget: approved?.budget || scenario.budget,
    annualTarget: approved ? approved.annualTarget : scenario.annualTarget
  }
}

export function createPlanningScenario(year, cutoff = 0) {
  const scenario = createBlankScenario(year)
  scenario.closedThroughMonth = cutoff
  scenario.baselineExcludesMeasures = false
  scenario.source = 'contracts'
  scenario.newLease = { enabled: false, startMonth: Math.min(cutoff + 1, 24), freeMonths: 0, termMonths: 36, monthlyRent: 0, collectionDelayMonths: 0 }
  scenario.costSaving = { enabled: false, startMonth: Math.min(cutoff + 1, 24), monthlySaving: 0, implementationCost: 0 }
  return scenario
}

export function applyFinancialForecast(scenario, forecast, { seedBudget = false } = {}) {
  if (forecast?.unit !== '万元' || !Array.isArray(forecast.months) || forecast.months.length !== 12) throw new Error('预测数据口径或月份不完整，请刷新后重试。')
  const seen = new Set()
  const rows = [...forecast.months].sort((a, b) => a.month - b.month).map(row => {
    if (!Number.isInteger(row.month) || row.month < 1 || row.month > 12 || seen.has(row.month)) throw new Error('预测月份不完整，请刷新后重试。')
    seen.add(row.month)
    if (typeof row.revenue !== 'number' || !Number.isFinite(row.revenue) || typeof row.operating_cost !== 'number' || !Number.isFinite(row.operating_cost)) throw new Error('预测金额尚不完整，请核对业务数据。')
    return { revenue: row.revenue, operatingCost: row.operating_cost }
  })
  const cutoff = forecast.closed_through_month
  if (!Number.isInteger(cutoff) || cutoff < 0 || cutoff > 12) throw new Error('预测截止月份无效。')
  const next = clonePlanningValue(scenario)
  next.year = forecast.year
  next.closedThroughMonth = cutoff
  next.actual = rows.map((row, index) => index < cutoff ? { ...row } : { revenue: null, operatingCost: null })
  next.forecast = rows.map((row, index) => index >= cutoff ? { ...row } : { revenue: null, operatingCost: null })
  if (seedBudget) {
    next.budget = rows.map(row => ({ ...row }))
    next.annualTarget = rows.reduce((sum, row) => sum + row.revenue - row.operatingCost, 0)
  }
  next.source = 'contracts'
  return next
}

export function versionBudgetReference(version, activeBudget) {
  const explicit = version?.budget_version_id || version?.snapshot?.budget_version_id || version?.client_state?.budgetVersionId
  if (Number.isInteger(Number(explicit)) && Number(explicit) > 0) return Number(explicit)
  const saved = version?.client_state?.scenario
  if (!saved || !activeBudget || version.year !== activeBudget.year || !Array.isArray(saved.budget) || saved.budget.length !== 12) return null
  const active = applyFinancialForecast(createPlanningScenario(activeBudget.year), activeBudget.snapshot, { seedBudget: true })
  active.annualTarget = activeBudget.client_state?.scenario?.annualTarget ?? active.annualTarget
  if (typeof saved.annualTarget !== 'number' || !Number.isFinite(saved.annualTarget) || Math.abs(saved.annualTarget - active.annualTarget) > 1e-8) return null
  const match = saved.budget.every((row, index) => ['revenue', 'operatingCost'].every(key =>
    typeof row[key] === 'number' && Number.isFinite(row[key]) && Math.abs(row[key] - active.budget[index][key]) < 1e-8
  ))
  return match ? activeBudget.id : null
}

export function financialForecastChanged(before, after) {
  if (!before || !after) return false
  const signature = value => JSON.stringify({
    year: value.year, cutoff: value.closed_through_month,
    sources: (value.sources || []).filter(item => item.source_type !== 'budget_adjustment').map(item => ({
      key: item.source_key,
      months: (item.months || []).map(row => [row.month, row.revenue, row.operating_cost])
    })).sort((a, b) => a.key.localeCompare(b.key))
  })
  return signature(before) !== signature(after)
}

export function expenseDraft(expense = {}, year = new Date().getFullYear()) {
  const draft = {
    id: expense.id || null, name: expense.name || '', category: expense.category || 'opex',
    amount: expense.amount_yuan === undefined ? null : expense.amount_yuan / 10000,
    occurrence_month: expense.occurrence_month || `${year}-01`,
    payment_month: expense.payment_month || expense.occurrence_month || `${year}-01`,
    paid_on: expense.paid_on || '', owner: expense.owner || '', notes: expense.notes || '',
    is_active: expense.is_active !== false, revision: expense.revision
  }
  draft.savedRequest = expense.id ? JSON.stringify(expenseRequest(draft)) : null
  return draft
}

export function expenseHasChanges(draft) {
  try { return !draft.id || JSON.stringify(expenseRequest(draft)) !== draft.savedRequest }
  catch { return true }
}

export function expenseRequest(draft) {
  if (!draft.name?.trim()) throw new Error('请填写费用名称。')
  if (draft.amount === null || draft.amount === '' || !Number.isFinite(Number(draft.amount)) || Number(draft.amount) < 0) throw new Error('请填写有效费用金额，空白不能作为零。')
  return {
    name: draft.name.trim(), category: draft.category, amount_yuan: Math.round(Number(draft.amount) * 1000000) / 100,
    occurrence_month: draft.occurrence_month, payment_month: draft.payment_month,
    paid_on: draft.paid_on || null, owner: draft.owner || '', notes: draft.notes || '',
    is_active: draft.is_active !== false, ...(draft.id ? { expected_revision: draft.revision } : {})
  }
}

export function buildPlanningBridge(result) {
  if (!result?.valid) return []
  const rows = result.months
  const details = (filter, calculate, kind) => rows.filter(filter).map(row => ({
    key: `${kind}:${row.month}`, month: row.month, label: `${row.month}月`, kind, value: calculate(row),
    budgetRevenue: row.budgetRevenue, budgetCost: row.budgetCost,
    latestRevenue: row.closed ? row.actualRevenue : row.forecastRevenue,
    latestCost: row.closed ? row.actualCost : row.forecastCost
  }))
  const definitions = [
    ['budget', '批准预算', true, details(() => true, row => row.budgetEbitda, 'budget')],
    ['actual', '已完成月份差异', false, details(row => row.closed, row => row.baselineEbitda - row.budgetEbitda, 'actual')],
    ['revenue', '后续收入差异', false, details(row => !row.closed, row => row.forecastRevenue - row.budgetRevenue, 'revenue')],
    ['expense', '后续费用差异', false, details(row => !row.closed, row => row.budgetCost - row.forecastCost, 'expense')],
    ['baseline', '当前基准预计', true, details(() => true, row => row.baselineEbitda, 'baseline')],
    ['lease', '新增租约', false, details(() => true, row => row.newLeaseRevenue, 'lease')],
    ['saving', '经营降本', false, details(() => true, row => row.costSaving, 'saving')],
    ['implementation', '一次性实施费', false, details(() => true, row => -row.implementationCost, 'implementation')],
    ['latest', '措施后预计', true, details(() => true, row => row.withMeasuresEbitda, 'latest')]
  ]
  let running = 0
  return definitions.map(([key, label, total, rows]) => {
    const value = rows.reduce((sum, row) => sum + row.value, 0)
    const start = total ? 0 : running
    const end = total ? value : running + value
    running = end
    return { key, label, total, value, start, end, details: rows }
  })
}

// Split floating bars at zero so a loss-making project is drawn faithfully too.
export function planningBridgeBars(bridge) {
  return bridge.map(item => {
    const low = Math.min(item.start, item.end)
    const high = Math.max(item.start, item.end)
    return {
      offset: low >= 0 ? low : high <= 0 ? high : 0,
      positive: Math.max(0, high) - Math.max(0, low),
      negative: Math.min(0, low) - Math.min(0, high)
    }
  })
}

export function buildSourcePlanningBase(comparison) {
  if (!comparison || !Array.isArray(comparison.all_sources)) return []
  const normalize = item => ({
    ...item, key: item.source_key, sourceType: item.source_type,
    contractId: item.contract_id || (item.source_type === 'contract' ? item.source_id : null),
    baseline: item.baseline_value, current: item.current_value, value: item.delta
  })
  const sources = (comparison.items || []).map(normalize)
  const allSources = comparison.all_sources.map(normalize)
  const definitions = [
    { key: 'budget', label: '批准预算', total: true, value: comparison.baseline_total, details: allSources.map(item => ({ ...item, value: item.baseline })) },
    ...['contract', 'expense', 'budget_adjustment'].map(type => {
      const details = sources.filter(item => item.sourceType === type)
      return { key: type, label: { contract: '合同收入变化', expense: '费用变化', budget_adjustment: '预算编制差异' }[type], total: false, value: details.reduce((sum, row) => sum + row.value, 0), details }
    })
  ]
  const other = sources.filter(item => !['contract', 'expense', 'budget_adjustment'].includes(item.sourceType))
  if (other.length) definitions.push({ key: 'other', label: '其他来源变化', total: false, value: other.reduce((sum, row) => sum + row.value, 0), details: other })
  definitions.push({ key: 'baseline', label: '当前基准预计', total: true, value: comparison.current_total, details: allSources.map(item => ({ ...item, value: item.current })) })
  return definitions
}

export function buildSourcePlanningBridge(comparison, result, sourceDefinitions = buildSourcePlanningBase(comparison)) {
  if (!comparison || !result?.valid || !sourceDefinitions.length) return []
  const definitions = [...sourceDefinitions]
  const currentDetails = sourceDefinitions.find(item => item.key === 'baseline')?.details || []
  const measures = buildPlanningBridge(result).filter(item => ['lease', 'saving', 'implementation'].includes(item.key)).map(item => ({
    ...item,
    details: [{ key: item.key, label: item.label, sourceType: 'measure', reason: '单独叠加的经营措施', baseline: 0, current: item.value, value: item.value, months: item.details.map(row => ({ month: row.month, baseline_value: 0, current_value: row.value, delta: row.value })) }]
  }))
  definitions.push(...measures)
  definitions.push({ key: 'latest', label: '措施后预计', total: true, value: comparison.current_total + result.annual.ebitdaIncrement, details: [
    ...currentDetails, ...measures.flatMap(item => item.details)
  ] })
  let running = 0
  return definitions.map(item => {
    const start = item.total ? 0 : running
    const end = item.total ? item.value : running + item.value
    running = end
    return { ...item, start, end }
  })
}

export function planningDrillRows(bridgeItem, month = 0) {
  if (!bridgeItem) return []
  return bridgeItem.details.map(item => {
    if (!month) return { ...item }
    const row = item.months?.find(row => row.month === Number(month))
    const baseline = row?.baseline_value ?? 0
    const current = row?.current_value ?? 0
    const value = bridgeItem.key === 'budget' ? baseline : ['baseline', 'latest'].includes(bridgeItem.key) ? current : row?.delta ?? 0
    return { ...item, baseline, current, value }
  }).filter(item => Math.abs(item.value) > 0.0000001 || Math.abs(item.baseline) > 0.0000001 || Math.abs(item.current) > 0.0000001)
}
