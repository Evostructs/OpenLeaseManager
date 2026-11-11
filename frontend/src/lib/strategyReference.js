export function applyStrategyReference(strategy, version, { hold = 0, renovate = 0, reason = '' } = {}) {
  if (version?.kind !== 'forecast') throw new Error('请选择已保存的预测版本。')
  const base = version.snapshot?.totals?.net_cash_flow
  if (base === null || base === undefined || !Number.isFinite(Number(base))) throw new Error('该版本缺少现金流口径，不能自动引用。')
  if ([hold, renovate].some(value => value === '' || !Number.isFinite(Number(value)))) throw new Error('请填写明确的调整金额，无调整填写0。')
  if ((Number(hold) || Number(renovate)) && !reason.trim()) throw new Error('请填写调整依据。')
  const round = value => Math.round((value + Number.EPSILON) * 1e6) / 1e6
  return { ...strategy, source: 'forecast_reference',
    hold: { ...strategy.hold, annualNetCashFlow: round(Number(base) + Number(hold)) },
    renovate: { ...strategy.renovate, annualNetCashFlow: round(Number(base) + Number(renovate)) },
    reference: { version_id: version.id, version_name: version.name, year: version.year, revision: version.revision,
      source_digest: version.source_digest, net_cash_flow: Number(base), property_fee_cash_included: version.snapshot?.coverage?.property_fee_cash_included,
      hold_adjustment_wan: Number(hold), renovate_adjustment_wan: Number(renovate), reason: reason.trim(),
      basis: 'saved_baseline_net_cash_flow_excluding_hypothetical_measures' }
  }
}
export function detachStrategyReference(strategy) {
  const { reference, ...manual } = strategy
  return { ...manual, source: 'manual' }
}
