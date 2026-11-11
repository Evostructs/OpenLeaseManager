/** Pure calculation helpers. All monetary values are 万元; persistence lives in the planning API. */
const MONTH_COUNT = 12
const MONEY_LIMIT = 1e12
const BUDGET_ASSUMPTIONS = [
  '金额单位为万元。合同与费用形成业务基准；批准预算独立冻结，经营措施作为增量计算，不直接修改正式合同。',
  '月度 EBITDA＝当月收入－经营费用；全年最新预计＝已结账月实际＋未结账月预测。此处不含折旧摊销、利息和所得税。',
  '新增租约从起租月开始，初始免租包含在租期内；固定合同总租金在整个租期直线确认收入。按当月应付租金于月末收款，可整体延后收款。',
  '新增租约未计入税费、佣金、装修补贴及额外运营成本，需在基准或另行测算中考虑；本模型假定收款可全额实现。',
  '降本从生效月起持续，每月节约经营费用并减少同额现金支出；实施费用在生效月一次费用化且支付。',
  '措施必须从未结账月份开始，且基准预测未包含这些措施；不追溯修改已结账实际，不重新分配批准预算。',
  '现金增量仅含本次措施新增收款和节约支出，不代表全项目现金流。下一年仅展示措施增量，没有下一年基准总额。'
]

const STRATEGY_ASSUMPTIONS = [
  '统一资产层无杠杆口径，金额为万元；不计债务、利息、融资现金流及所得税。交易费用必须由输入明确包含适用处置税费。',
  '现在出售发生于期初；持有和改造的经营净现金流均在每年年末发生，退出净额在同一比较期间的最后一年年末实现。',
  '改造投入发生于期初，第一年停租净现金流损失在第一年年末扣减。持有和改造的年度净现金流、成长与退出价格分别显式输入。',
  '年度净现金流须已扣除经营支出与必要的经常性资本开支；退出价格是终值假设，不由 EBITDA 自动推导。',
  '按设定的现金流、投入和退出价格计算各路径净现值。'
]

function numeric(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string' || !value.trim()) return null
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim())) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function readNumber(value, label, errors, { min = -MONEY_LIMIT, max = MONEY_LIMIT, integer = false } = {}) {
  const number = numeric(value)
  if (number === null) {
    errors.push(`${label}请填写有效数字，空白不能作为零。`)
    return null
  }
  if (integer && !Number.isInteger(number)) errors.push(`${label}须为整数。`)
  if (number < min || number > max) errors.push(`${label}须在 ${min} 至 ${max} 之间。`)
  return number
}

function readMoney(value, label, errors, nonnegative = true) {
  return readNumber(value, label, errors, { min: nonnegative ? 0 : -MONEY_LIMIT })
}

function readMonthlyRows(rows, name, errors, required) {
  if (!Array.isArray(rows) || rows.length !== MONTH_COUNT) {
    errors.push(`${name}须包含完整的 12 个月。`)
    return Array.from({ length: MONTH_COUNT }, () => null)
  }
  return Array.from({ length: MONTH_COUNT }, (_, index) => {
    if (!required(index + 1)) return null
    const row = rows[index]
    return {
      revenue: readMoney(row?.revenue, `${name}${index + 1}月收入`, errors),
      operatingCost: readMoney(row?.operatingCost, `${name}${index + 1}月经营费用`, errors)
    }
  })
}

function blankRows() {
  return Array.from({ length: MONTH_COUNT }, () => ({ revenue: null, operatingCost: null }))
}

export function createBlankScenario(year = new Date().getFullYear()) {
  return {
    source: 'manual',
    year,
    closedThroughMonth: 0,
    annualTarget: null,
    baselineExcludesMeasures: false,
    budget: blankRows(),
    actual: blankRows(),
    forecast: blankRows(),
    newLease: { enabled: false, startMonth: null, freeMonths: null, termMonths: null, monthlyRent: null, collectionDelayMonths: 0 },
    costSaving: { enabled: false, startMonth: null, monthlySaving: null, implementationCost: null }
  }
}

export function createDemoScenario(year = new Date().getFullYear()) {
  const scenario = createBlankScenario(year)
  const budgetEbitda = [70, 72, 75, 78, 80, 83, 86, 88, 91, 97, 111, 119]
  const baselineEbitda = [58, 60, 63, 66, 68, 70, 73, 74, 82, 88, 94, 104]
  const costs = [30, 28, 30, 29, 31, 33, 35, 32, 34, 36, 34, 38]
  return {
    ...scenario,
    source: 'demo',
    label: '演示假设：截至8月底，全部金额均为示例',
    closedThroughMonth: 8,
    annualTarget: 1050,
    baselineExcludesMeasures: true,
    budget: budgetEbitda.map((amount, index) => ({ revenue: amount + costs[index], operatingCost: costs[index] })),
    actual: baselineEbitda.map((amount, index) => index < 8 ? { revenue: amount + costs[index], operatingCost: costs[index] } : { revenue: null, operatingCost: null }),
    forecast: baselineEbitda.map((amount, index) => index >= 8 ? { revenue: amount + costs[index], operatingCost: costs[index] } : { revenue: null, operatingCost: null }),
    newLease: { enabled: true, startMonth: 9, freeMonths: 2, termMonths: 36, monthlyRent: 30, collectionDelayMonths: 0 },
    costSaving: { enabled: true, startMonth: 10, monthlySaving: 8, implementationCost: 6 }
  }
}

function readMeasures(input, cutoff, errors) {
  const leaseInput = input.newLease || { enabled: false }
  const savingInput = input.costSaving || { enabled: false }
  if (typeof leaseInput.enabled !== 'boolean') errors.push('新增租约的启用状态须明确选择。')
  if (typeof savingInput.enabled !== 'boolean') errors.push('降本措施的启用状态须明确选择。')
  const lease = { enabled: leaseInput.enabled === true }
  const saving = { enabled: savingInput.enabled === true }
  if ((lease.enabled || saving.enabled) && input.baselineExcludesMeasures !== true) {
    errors.push('请先确认基准预测未包含本次新增租约和降本措施，避免重复计入。')
  }
  if (lease.enabled) {
    lease.startMonth = readNumber(leaseInput.startMonth, '新增租约起租月份', errors, { min: 1, max: 24, integer: true })
    lease.termMonths = readNumber(leaseInput.termMonths, '新增租约租期（月）', errors, { min: 1, max: 600, integer: true })
    lease.freeMonths = readNumber(leaseInput.freeMonths, '新增租约免租月数', errors, { min: 0, max: 600, integer: true })
    lease.monthlyRent = readMoney(leaseInput.monthlyRent, '新增租约月租金', errors)
    lease.collectionDelayMonths = readNumber(leaseInput.collectionDelayMonths, '收款延迟月数', errors, { min: 0, max: 120, integer: true })
    if (lease.freeMonths !== null && lease.termMonths !== null && lease.freeMonths > lease.termMonths) errors.push('免租月数不能超过包含免租期的总租期。')
    if (lease.startMonth !== null && cutoff !== null && lease.startMonth <= cutoff) errors.push('新增租约须从未结账月份起租，已发生租约请纳入基准，不能作为新措施重复计入。')
  }
  if (saving.enabled) {
    saving.startMonth = readNumber(savingInput.startMonth, '降本生效月份', errors, { min: 1, max: 24, integer: true })
    saving.monthlySaving = readMoney(savingInput.monthlySaving, '每月降本金额', errors)
    saving.implementationCost = readMoney(savingInput.implementationCost, '降本一次性实施费用', errors)
    if (saving.startMonth !== null && cutoff !== null && saving.startMonth <= cutoff) errors.push('降本须从未结账月份生效，已经发生的节约及实施费用请纳入实际或基准，不能追溯补计。')
  }
  return { lease, saving }
}

function measureForMonth(month, lease, saving) {
  const leaseActive = lease.enabled && month >= lease.startMonth && month < lease.startMonth + lease.termMonths
  const payableMonth = lease.enabled ? month - lease.collectionDelayMonths : null
  const cashDue = lease.enabled && payableMonth >= lease.startMonth + lease.freeMonths && payableMonth < lease.startMonth + lease.termMonths
  const savingActive = saving.enabled && month >= saving.startMonth
  const newLeaseRevenue = leaseActive ? lease.monthlyRent * (lease.termMonths - lease.freeMonths) / lease.termMonths : 0
  const costSaving = savingActive ? saving.monthlySaving : 0
  const implementationCost = saving.enabled && month === saving.startMonth ? saving.implementationCost : 0
  const newLeaseCash = cashDue ? lease.monthlyRent : 0
  const costSavingCash = costSaving - implementationCost
  return {
    newLeaseRevenue,
    costSaving,
    implementationCost,
    ebitdaIncrement: newLeaseRevenue + costSaving - implementationCost,
    newLeaseCash,
    costSavingCash,
    cashIncrement: newLeaseCash + costSavingCash
  }
}

const MEASURE_TOTALS = ['newLeaseRevenue', 'costSaving', 'implementationCost', 'ebitdaIncrement', 'newLeaseCash', 'costSavingCash', 'cashIncrement']

function sumKey(rows, key) {
  return rows.reduce((sum, row) => sum + row[key], 0)
}

function measureTotals(rows) {
  return Object.fromEntries(MEASURE_TOTALS.map(key => [key, sumKey(rows, key)]))
}

export function calculateBudgetScenario(input) {
  const errors = []
  const invalid = () => ({ valid: false, errors, months: [], annual: null, nextYear: null, assumptions: [...BUDGET_ASSUMPTIONS] })
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    errors.push('请提供完整的预算测算输入。')
    return invalid()
  }
  const year = readNumber(input.year, '测算年份', errors, { min: 1900, max: 9998, integer: true })
  const cutoff = readNumber(input.closedThroughMonth, '已结账至月份', errors, { min: 0, max: 12, integer: true })
  const annualTarget = readMoney(input.annualTarget, '独立年度 EBITDA 目标', errors, false)
  const budget = readMonthlyRows(input.budget, '批准预算', errors, () => true)
  const actual = readMonthlyRows(input.actual, '实际', errors, month => cutoff !== null && month <= cutoff)
  const forecast = readMonthlyRows(input.forecast, '预测', errors, month => cutoff !== null && month > cutoff)
  const { lease, saving } = readMeasures(input, cutoff, errors)
  if (saving.enabled && saving.monthlySaving !== null && saving.startMonth !== null) {
    forecast.forEach((row, index) => {
      if (row?.operatingCost !== null && row?.operatingCost !== undefined && index + 1 >= saving.startMonth && saving.monthlySaving > row.operatingCost) {
        errors.push(`${index + 1}月降本金额超过该月基准经营费用，请核对可节约成本范围。`)
      }
    })
  }
  if (errors.length) return invalid()
  const months = budget.map((row, index) => {
    const month = index + 1
    const closed = month <= cutoff
    const active = closed ? actual[index] : forecast[index]
    const baselineEbitda = active.revenue - active.operatingCost
    const measures = measureForMonth(month, lease, saving)
    return {
      month, label: `${year}年${month}月`, closed,
      budgetRevenue: row.revenue, budgetCost: row.operatingCost, budgetEbitda: row.revenue - row.operatingCost,
      actualRevenue: actual[index]?.revenue ?? null, actualCost: actual[index]?.operatingCost ?? null,
      actualEbitda: closed ? baselineEbitda : null,
      forecastRevenue: forecast[index]?.revenue ?? null, forecastCost: forecast[index]?.operatingCost ?? null,
      forecastEbitda: closed ? null : baselineEbitda,
      baselineEbitda, ...measures, withMeasuresEbitda: baselineEbitda + measures.ebitdaIncrement
    }
  })
  const budgetEbitda = sumKey(months, 'budgetEbitda')
  const baselineForecast = sumKey(months, 'baselineEbitda')
  const totals = measureTotals(months)
  const annual = {
    budgetEbitda, annualTarget, budgetTargetGap: annualTarget - budgetEbitda,
    actualToDate: sumKey(months.filter(row => row.closed), 'baselineEbitda'),
    remainingForecast: sumKey(months.filter(row => !row.closed), 'baselineEbitda'),
    baselineForecast, ...totals, withMeasures: baselineForecast + totals.ebitdaIncrement,
    targetGapBefore: annualTarget - baselineForecast,
    targetGapAfter: annualTarget - baselineForecast - totals.ebitdaIncrement
  }
  const nextYearMonths = Array.from({ length: MONTH_COUNT }, (_, index) => ({
    month: index + 1,
    label: `${year + 1}年${index + 1}月`,
    ...measureForMonth(index + 13, lease, saving)
  }))
  return { valid: true, errors: [], months, annual, nextYear: { year: year + 1, months: nextYearMonths, ...measureTotals(nextYearMonths) }, assumptions: [...BUDGET_ASSUMPTIONS] }
}

export function createBlankAssetStrategy() {
  return {
    source: 'manual', propertyType: 'own', hasDisposalRight: false,
    years: null, discountRate: null,
    hold: { annualNetCashFlow: null, annualGrowthRate: null, exitPrice: null, exitCostRate: null },
    renovate: { capex: null, annualNetCashFlow: null, annualGrowthRate: null, firstYearRentLoss: null, exitPrice: null, exitCostRate: null },
    sell: { price: null, transactionCost: null }
  }
}

export function createDemoAssetStrategy() {
  return {
    source: 'demo', label: '演示假设：自持资产，具备处置权', propertyType: 'own', hasDisposalRight: true,
    years: 5, discountRate: 8,
    hold: { annualNetCashFlow: 900, annualGrowthRate: 2, exitPrice: 15000, exitCostRate: 3 },
    renovate: { capex: 1500, annualNetCashFlow: 1150, annualGrowthRate: 3, firstYearRentLoss: 300, exitPrice: 18000, exitCostRate: 3 },
    sell: { price: 15000, transactionCost: 450 }
  }
}

function readHoldingStrategy(value, label, errors) {
  return {
    annualNetCashFlow: readMoney(value?.annualNetCashFlow, `${label}首年年度净现金流`, errors, false),
    annualGrowthRate: readNumber(value?.annualGrowthRate, `${label}年度净现金流增长率（%）`, errors, { min: -100, max: 100 }),
    exitPrice: readMoney(value?.exitPrice, `${label}期末退出价格`, errors),
    exitCostRate: readNumber(value?.exitCostRate, `${label}期末交易费用率（%）`, errors, { min: 0, max: 100 })
  }
}

function holdingCashFlows(strategy, years, discountRate, firstYearRentLoss = 0) {
  return Array.from({ length: years }, (_, index) => {
    const year = index + 1
    const operatingCashFlow = strategy.annualNetCashFlow * (1 + strategy.annualGrowthRate / 100) ** index - (year === 1 ? firstYearRentLoss : 0)
    const disposalCashFlow = year === years ? strategy.exitPrice * (1 - strategy.exitCostRate / 100) : 0
    const totalCashFlow = operatingCashFlow + disposalCashFlow
    const discountFactor = 1 / (1 + discountRate / 100) ** year
    return { year, operatingCashFlow, disposalCashFlow, totalCashFlow, discountFactor, presentValue: totalCashFlow * discountFactor }
  })
}

export function compareAssetStrategies(input) {
  const errors = []
  const base = { applicable: false, errors, strategies: [], ranking: [], assumptions: [...STRATEGY_ASSUMPTIONS] }
  if (!input || typeof input !== 'object' || Array.isArray(input)) return { ...base, valid: false, errors: ['请提供完整的项目策略假设。'] }
  if (!['own', 'leased', 'managed'].includes(input.propertyType)) errors.push('请选择自持、租入运营或受托管理项目。')
  if (typeof input.hasDisposalRight !== 'boolean') errors.push('请明确项目是否具备资产处置权。')
  if (errors.length) return { ...base, valid: false }
  if (input.propertyType !== 'own' || !input.hasDisposalRight) {
    const reason = input.propertyType === 'leased'
      ? '租入运营项目应按租赁合同评估续租、转租、经营调整或退出，本模型不比较资产出售。'
      : input.propertyType === 'managed'
        ? '受托管理项目应按管理合同评估续约、收费调整或退出，本模型不比较资产出售。'
        : '尚未确认资产处置权，暂不展示资产持有、改造与出售净现值比较。'
    return { ...base, valid: true, reason }
  }
  const years = readNumber(input.years, '统一比较年限', errors, { min: 1, max: 50, integer: true })
  const discountRate = readNumber(input.discountRate, '折现率（%）', errors, { min: 0, max: 100 })
  const hold = readHoldingStrategy(input.hold, '继续持有', errors)
  const renovate = {
    ...readHoldingStrategy(input.renovate, '改造后持有', errors),
    capex: readMoney(input.renovate?.capex, '期初改造投入', errors),
    firstYearRentLoss: readMoney(input.renovate?.firstYearRentLoss, '改造首年停租净现金流损失', errors)
  }
  const sellPrice = readMoney(input.sell?.price, '现在出售价格', errors)
  const sellCost = readMoney(input.sell?.transactionCost, '现在出售交易费用', errors)
  if (errors.length) return { ...base, valid: false, applicable: true }
  const holdFlows = holdingCashFlows(hold, years, discountRate)
  const renovateFlows = holdingCashFlows(renovate, years, discountRate, renovate.firstYearRentLoss)
  const strategies = [
    { key: 'hold', label: '继续持有', initialCashFlow: 0, yearlyCashFlows: holdFlows, npv: sumKey(holdFlows, 'presentValue') },
    { key: 'renovate', label: '改造后持有', initialCashFlow: -renovate.capex, yearlyCashFlows: renovateFlows, npv: -renovate.capex + sumKey(renovateFlows, 'presentValue') },
    { key: 'sell', label: '现在出售', initialCashFlow: sellPrice - sellCost, yearlyCashFlows: [], npv: sellPrice - sellCost }
  ]
  const ranking = [...strategies].sort((a, b) => b.npv - a.npv)
  const tied = ranking.filter(row => Math.abs(row.npv - ranking[0].npv) <= 1e-8)
  const conclusion = tied.length > 1
    ? `按当前方案参数，${tied.map(row => row.label).join('、')}的净现值并列较高。`
    : `按当前方案参数，${ranking[0].label}的净现值较高。`
  return { ...base, valid: true, applicable: true, years, discountRate, strategies, ranking, conclusion }
}
