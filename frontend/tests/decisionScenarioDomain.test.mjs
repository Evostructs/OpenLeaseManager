import assert from 'node:assert/strict'
import test from 'node:test'

import {
  calculateBudgetScenario,
  compareAssetStrategies,
  createBlankAssetStrategy,
  createBlankScenario,
  createDemoAssetStrategy,
  createDemoScenario
} from '../src/lib/decisionScenarioDomain.js'

function closeTo(actual, expected, message) {
  assert.ok(Math.abs(actual - expected) < 1e-8, message || `${actual} should equal ${expected}`)
}

function zeroScenario(cutoff = 0) {
  const input = createBlankScenario(2026)
  input.closedThroughMonth = cutoff
  input.annualTarget = 0
  input.baselineExcludesMeasures = true
  input.budget = Array.from({ length: 12 }, () => ({ revenue: 0, operatingCost: 0 }))
  input.actual = Array.from({ length: 12 }, () => ({ revenue: 0, operatingCost: 0 }))
  input.forecast = Array.from({ length: 12 }, () => ({ revenue: 0, operatingCost: 0 }))
  return input
}

function leaseScenario(lease) {
  const input = zeroScenario()
  input.newLease = { enabled: true, startMonth: 1, freeMonths: 0, termMonths: 12, monthlyRent: 30, collectionDelayMonths: 0, ...lease }
  return input
}

test('demo retains uneven approved budgets and reconciles the independent target', () => {
  const input = createDemoScenario(2026)
  const result = calculateBudgetScenario(input)
  assert.equal(result.valid, true)
  assert.equal(result.annual.budgetEbitda, 1050)
  assert.equal(result.annual.budgetTargetGap, 0)
  assert.notEqual(result.months[0].budgetEbitda, result.months[11].budgetEbitda)
  assert.equal(result.annual.actualToDate, 532)
  assert.equal(result.annual.remainingForecast, 368)
  assert.equal(result.annual.baselineForecast, 900)
  closeTo(result.annual.newLeaseRevenue, 30 * 34 / 36 * 4)
  assert.equal(result.annual.costSaving, 24)
  assert.equal(result.annual.implementationCost, 6)
  closeTo(result.annual.withMeasures, 1031 + 1 / 3)
  closeTo(result.annual.targetGapAfter, 18 + 2 / 3)
  assert.equal(result.annual.newLeaseCash, 60)
  assert.equal(result.annual.cashIncrement, 78)
  closeTo(result.nextYear.ebitdaIncrement, 436)
  assert.equal(result.nextYear.cashIncrement, 456)
  assert.equal(result.nextYear.year, 2027)
  assert.equal('withMeasures' in result.nextYear, false, 'no next-year project total without a next-year baseline')
})

test('annual target mismatch stays visible instead of spreading the difference over months', () => {
  const input = createDemoScenario(2026)
  input.annualTarget = 1200
  const result = calculateBudgetScenario(input)
  assert.equal(result.annual.budgetTargetGap, 150)
  assert.equal(result.months[0].budgetEbitda, 70)
  assert.equal(result.annual.targetGapBefore, 300)
})

test('blank values block calculation; explicit zero is valid and targets may be negative', () => {
  const blank = calculateBudgetScenario(createBlankScenario(2026))
  assert.equal(blank.valid, false)
  assert.equal(blank.annual, null)
  assert.ok(blank.errors.some(error => error.includes('空白不能作为零')))
  const input = zeroScenario()
  input.annualTarget = -15
  const result = calculateBudgetScenario(input)
  assert.equal(result.valid, true)
  assert.equal(result.annual.withMeasures, 0)
  assert.equal(result.annual.targetGapAfter, -15)
})

test('active rows reject empty, malformed and non-finite values without silently using zero', () => {
  for (const value of [null, undefined, '', '   ', NaN, Infinity, -Infinity, 'Infinity', '0x10', [], {}, true]) {
    const input = zeroScenario()
    input.forecast[0].revenue = value
    const result = calculateBudgetScenario(input)
    assert.equal(result.valid, false, `invalid value ${String(value)}`)
    assert.equal(result.annual, null)
  }
  const input = zeroScenario()
  input.forecast[0].revenue = '1.25'
  assert.equal(calculateBudgetScenario(input).annual.baselineForecast, 1.25)
})

test('all budgets are required while unused actual and forecast rows remain null in output', () => {
  const input = createDemoScenario(2026)
  input.actual[10].revenue = Infinity
  input.forecast[0].revenue = 'not used'
  const result = calculateBudgetScenario(input)
  assert.equal(result.valid, true)
  assert.equal(result.months[10].actualRevenue, null)
  assert.equal(result.months[0].forecastRevenue, null)
  input.budget[11].operatingCost = null
  assert.equal(calculateBudgetScenario(input).valid, false)
})

test('roll-forward replaces closed-month forecast with actual, preserving original budgets', () => {
  const input = zeroScenario(1)
  input.actual[0] = { revenue: 100, operatingCost: 40 }
  input.forecast[0] = { revenue: 999, operatingCost: 0 }
  input.forecast[1] = { revenue: 50, operatingCost: 10 }
  const result = calculateBudgetScenario(input)
  assert.equal(result.annual.actualToDate, 60)
  assert.equal(result.annual.remainingForecast, 40)
  assert.equal(result.annual.baselineForecast, 100)
  assert.equal(result.months[0].withMeasuresEbitda, 60)
  assert.equal(result.months[0].budgetEbitda, 0)
})

test('all closed and no closed month boundaries retain the correct baseline', () => {
  for (const cutoff of [0, 12]) {
    const input = zeroScenario(cutoff)
    input.actual.forEach(row => { row.revenue = 10 })
    input.forecast.forEach(row => { row.revenue = 20 })
    const result = calculateBudgetScenario(input)
    assert.equal(result.valid, true)
    assert.equal(result.annual.baselineForecast, cutoff === 12 ? 120 : 240)
    assert.equal(result.annual.actualToDate, cutoff === 12 ? 120 : 0)
  }
})

test('straight-line rent includes the initial free period while collection waits until payable months', () => {
  const result = calculateBudgetScenario(leaseScenario({ startMonth: 9, freeMonths: 2, termMonths: 36 }))
  closeTo(result.months[8].newLeaseRevenue, 30 * 34 / 36)
  assert.equal(result.months[8].newLeaseCash, 0)
  assert.equal(result.months[9].newLeaseCash, 0)
  assert.equal(result.months[10].newLeaseCash, 30)
  assert.equal(result.months[11].newLeaseCash, 30)
  closeTo(result.annual.ebitdaIncrement, 113 + 1 / 3)
})

test('January and September starts affect the current year differently, never annualize both', () => {
  const january = calculateBudgetScenario(leaseScenario({ startMonth: 1, freeMonths: 2, termMonths: 36 }))
  const september = calculateBudgetScenario(leaseScenario({ startMonth: 9, freeMonths: 2, termMonths: 36 }))
  closeTo(january.annual.newLeaseRevenue, 340)
  assert.equal(january.annual.newLeaseCash, 300)
  closeTo(september.annual.newLeaseRevenue, 113 + 1 / 3)
  assert.equal(september.annual.newLeaseCash, 60)
})

test('cross-year short lease stops revenue at expiry and collects outstanding rent after expiry', () => {
  const result = calculateBudgetScenario(leaseScenario({ startMonth: 12, freeMonths: 1, termMonths: 3, monthlyRent: 30, collectionDelayMonths: 2 }))
  assert.equal(result.annual.newLeaseRevenue, 20)
  assert.equal(result.annual.newLeaseCash, 0)
  assert.deepEqual(result.nextYear.months.slice(0, 5).map(row => row.newLeaseRevenue), [20, 20, 0, 0, 0])
  assert.deepEqual(result.nextYear.months.slice(0, 5).map(row => row.newLeaseCash), [0, 0, 30, 30, 0])
  assert.equal(result.nextYear.newLeaseRevenue, 40)
  assert.equal(result.nextYear.newLeaseCash, 60)
})

test('entirely free and zero-rent leases have no income or collections', () => {
  for (const lease of [{ termMonths: 36, freeMonths: 36 }, { monthlyRent: 0, freeMonths: 0 }]) {
    const result = calculateBudgetScenario(leaseScenario(lease))
    assert.equal(result.valid, true)
    assert.equal(result.annual.newLeaseRevenue, 0)
    assert.equal(result.annual.newLeaseCash, 0)
    assert.equal(result.nextYear.newLeaseCash, 0)
  }
})

test('cash delay never changes accounting revenue', () => {
  const immediate = calculateBudgetScenario(leaseScenario({ startMonth: 9, freeMonths: 2, termMonths: 36 }))
  const delayed = calculateBudgetScenario(leaseScenario({ startMonth: 9, freeMonths: 2, termMonths: 36, collectionDelayMonths: 2 }))
  assert.equal(immediate.annual.newLeaseRevenue, delayed.annual.newLeaseRevenue)
  assert.equal(delayed.annual.newLeaseCash, 0)
  assert.equal(delayed.nextYear.months[0].newLeaseCash, 30)
})

test('measures may start next year without improving the current-year performance', () => {
  const input = leaseScenario({ startMonth: 13, termMonths: 12, freeMonths: 2 })
  input.costSaving = { enabled: true, startMonth: 14, monthlySaving: 2, implementationCost: 3 }
  const result = calculateBudgetScenario(input)
  assert.equal(result.valid, true)
  assert.equal(result.annual.ebitdaIncrement, 0)
  assert.equal(result.annual.cashIncrement, 0)
  assert.equal(result.nextYear.newLeaseRevenue, 300)
  assert.equal(result.nextYear.costSaving, 22)
  assert.equal(result.nextYear.implementationCost, 3)
})

test('cost saving is prorated by active month and includes implementation expense once', () => {
  const input = zeroScenario(8)
  input.forecast.forEach(row => { row.operatingCost = 20 })
  input.costSaving = { enabled: true, startMonth: 10, monthlySaving: 8, implementationCost: 30 }
  const result = calculateBudgetScenario(input)
  assert.equal(result.annual.costSaving, 24)
  assert.equal(result.annual.implementationCost, 30)
  assert.equal(result.annual.ebitdaIncrement, -6)
  assert.equal(result.annual.cashIncrement, -6)
  assert.equal(result.months[9].ebitdaIncrement, -22)
  assert.equal(result.nextYear.implementationCost, 0)
  assert.equal(result.nextYear.costSaving, 96)
})

test('closed-month measures and excess savings are rejected instead of revising actual', () => {
  for (const key of ['newLease', 'costSaving']) {
    const input = createDemoScenario(2026)
    input[key].startMonth = 8
    const result = calculateBudgetScenario(input)
    assert.equal(result.valid, false)
    assert.ok(result.errors.some(error => error.includes('未结账月份')))
  }
  const input = createDemoScenario(2026)
  input.costSaving.monthlySaving = 999
  assert.ok(calculateBudgetScenario(input).errors.some(error => error.includes('超过该月基准经营费用')))
})

test('measures require explicit assurance they are not already in baseline', () => {
  const input = createDemoScenario(2026)
  for (const confirmation of [undefined, false, 'true', 1]) {
    input.baselineExcludesMeasures = confirmation
    const result = calculateBudgetScenario(input)
    assert.equal(result.valid, false)
    assert.ok(result.errors.some(error => error.includes('重复计入')))
  }
  input.newLease.enabled = false
  input.costSaving.enabled = false
  assert.equal(calculateBudgetScenario(input).valid, true)
})

test('invalid lease lengths, month bounds, amounts and fractional periods return errors', () => {
  for (const change of [
    { termMonths: 0 }, { termMonths: 2, freeMonths: 3 }, { freeMonths: -1 },
    { startMonth: 25 }, { startMonth: 0 }, { startMonth: 9.5 },
    { monthlyRent: -1 }, { monthlyRent: Infinity }, { collectionDelayMonths: -1 },
    { collectionDelayMonths: '' }, { freeMonths: 1.5 }, { enabled: 'true' }
  ]) assert.equal(calculateBudgetScenario(leaseScenario(change)).valid, false, JSON.stringify(change))
})

test('malformed root, row count, cutoff and negative operating input are rejected', () => {
  for (const input of [null, undefined, [], true]) assert.equal(calculateBudgetScenario(input).valid, false)
  for (const cutoff of [-1, 13, 2.5, null]) {
    const input = zeroScenario()
    input.closedThroughMonth = cutoff
    assert.equal(calculateBudgetScenario(input).valid, false)
  }
  const short = zeroScenario()
  short.budget.pop()
  assert.equal(calculateBudgetScenario(short).valid, false)
  const sparse = zeroScenario()
  sparse.budget = new Array(12)
  assert.equal(calculateBudgetScenario(sparse).valid, false)
  const negative = zeroScenario()
  negative.forecast[0].operatingCost = -1
  assert.equal(calculateBudgetScenario(negative).valid, false)
})

test('calculation never mutates the approved budget, baseline or another scenario instance', () => {
  const input = createDemoScenario(2026)
  const original = JSON.stringify(input)
  const first = calculateBudgetScenario(input)
  calculateBudgetScenario(input)
  assert.equal(JSON.stringify(input), original)
  first.months[0].budgetEbitda = 999
  assert.equal(calculateBudgetScenario(input).months[0].budgetEbitda, 70)
  const another = createDemoScenario(2026)
  input.budget[0].revenue = 0
  assert.equal(another.budget[0].revenue, 100)
})

test('intermediate rent values retain precision rather than rounding every month', () => {
  const result = calculateBudgetScenario(leaseScenario({ monthlyRent: 1, termMonths: 3, freeMonths: 1 }))
  assert.equal(result.months[0].newLeaseRevenue, 2 / 3)
  assert.equal(result.annual.newLeaseRevenue, 2)
  assert.equal(result.annual.newLeaseCash, 2)
})

test('strategy blank state needs authority and never turns missing financial inputs into zeros', () => {
  const blank = createBlankAssetStrategy()
  const unavailable = compareAssetStrategies(blank)
  assert.equal(unavailable.valid, true)
  assert.equal(unavailable.applicable, false)
  blank.hasDisposalRight = true
  const invalid = compareAssetStrategies(blank)
  assert.equal(invalid.valid, false)
  assert.equal(invalid.strategies.length, 0)
  assert.ok(invalid.errors.some(error => error.includes('空白不能作为零')))
})

test('leased and managed projects never receive an asset sale comparison even with disposal checkbox', () => {
  for (const propertyType of ['leased', 'managed']) {
    const input = createDemoAssetStrategy()
    input.propertyType = propertyType
    const result = compareAssetStrategies(input)
    assert.equal(result.valid, true)
    assert.equal(result.applicable, false)
    assert.equal(result.strategies.length, 0)
    assert.ok(result.reason.includes('不比较资产出售'))
  }
})

test('hold, renovate and sale use one horizon with explicit year-end discounting', () => {
  const input = createDemoAssetStrategy()
  input.years = 2
  input.discountRate = 10
  input.hold = { annualNetCashFlow: 100, annualGrowthRate: 10, exitPrice: 1000, exitCostRate: 10 }
  input.renovate = { capex: 200, annualNetCashFlow: 200, annualGrowthRate: 0, firstYearRentLoss: 50, exitPrice: 1200, exitCostRate: 10 }
  input.sell = { price: 950, transactionCost: 50 }
  const result = compareAssetStrategies(input)
  assert.equal(result.valid, true)
  const [hold, renovate, sell] = result.strategies
  closeTo(hold.npv, 100 / 1.1 + (110 + 900) / 1.1 ** 2)
  closeTo(renovate.npv, -200 + 150 / 1.1 + (200 + 1080) / 1.1 ** 2)
  assert.equal(sell.npv, 900)
  assert.equal(sell.initialCashFlow, 900)
  assert.equal(sell.yearlyCashFlows.length, 0)
  assert.equal(hold.yearlyCashFlows[0].disposalCashFlow, 0)
  assert.equal(hold.yearlyCashFlows[1].disposalCashFlow, 900)
  assert.equal(renovate.yearlyCashFlows[0].operatingCashFlow, 150)
  assert.equal(renovate.yearlyCashFlows[1].operatingCashFlow, 200)
  assert.equal(result.ranking[0].key, 'renovate')
  assert.ok(result.conclusion.includes('按当前方案参数'))
  assert.ok(result.conclusion.includes(result.ranking[0].label))
})

test('zero discount and negative net operating cash flow remain meaningful', () => {
  const input = createDemoAssetStrategy()
  input.years = 2
  input.discountRate = 0
  input.hold = { annualNetCashFlow: -100, annualGrowthRate: 0, exitPrice: 1000, exitCostRate: 0 }
  const result = compareAssetStrategies(input)
  assert.equal(result.valid, true)
  assert.equal(result.strategies[0].npv, 800)
})

test('NPV ranking allows ties and does not manufacture a unique winner', () => {
  const input = createDemoAssetStrategy()
  input.years = 1
  input.discountRate = 0
  input.hold = { annualNetCashFlow: 0, annualGrowthRate: 0, exitPrice: 100, exitCostRate: 0 }
  input.renovate = { ...input.hold, capex: 0, firstYearRentLoss: 0 }
  input.sell = { price: 100, transactionCost: 0 }
  const result = compareAssetStrategies(input)
  assert.equal(result.valid, true)
  assert.ok(result.conclusion.includes('并列'))
})

test('strategy validation rejects non-finite, missing, out-of-range and unsupported assumptions', () => {
  for (const mutate of [
    input => { input.propertyType = 'other' },
    input => { input.hasDisposalRight = 'true' },
    input => { input.years = 0 },
    input => { input.years = 1.5 },
    input => { input.discountRate = -100 },
    input => { input.discountRate = '' },
    input => { input.hold.annualNetCashFlow = Infinity },
    input => { input.hold.annualGrowthRate = -101 },
    input => { input.hold.exitCostRate = 101 },
    input => { input.renovate.capex = -1 },
    input => { input.renovate.firstYearRentLoss = null },
    input => { input.sell.transactionCost = NaN }
  ]) {
    const input = createDemoAssetStrategy()
    mutate(input)
    const result = compareAssetStrategies(input)
    assert.equal(result.valid, false)
    assert.equal(result.strategies.length, 0)
  }
})

test('strategy calculations are pure and use separate hold and renovation exit assumptions', () => {
  const input = createDemoAssetStrategy()
  const before = JSON.stringify(input)
  const baseline = compareAssetStrategies(input)
  assert.equal(JSON.stringify(input), before)
  input.renovate.exitPrice += 1000
  const changed = compareAssetStrategies(input)
  assert.equal(changed.strategies[0].npv, baseline.strategies[0].npv)
  assert.equal(changed.strategies[2].npv, baseline.strategies[2].npv)
  closeTo(changed.strategies[1].npv - baseline.strategies[1].npv, 970 / 1.08 ** 5)
})
