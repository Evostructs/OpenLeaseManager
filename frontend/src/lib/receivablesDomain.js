export function sumReceivableAmount(rows, key) {
  return rows.reduce((total, row) => total + Number(row?.[key] || 0), 0)
}

export function isReceivableOverdue(row) {
  return Number(row?.overdue_balance || 0) > 0 || Boolean(row?.is_overdue)
}

export function hasReceivablePendingInvoice(row) {
  return ['未开票', '待开票'].includes(row?.invoice_status)
}

export function hasReceivableFollowUpDue(row) {
  return Boolean(row?.follow_up_due)
}

export function hasReceivableActiveDunning(row) {
  return Boolean(row?.requires_dunning)
    || ['已提醒', '催收中', '已升级'].includes(row?.dunning_status)
}

export function hasReceivableFollowUp(row) {
  return Boolean(row?.follow_up_due)
    || Boolean(row?.requires_dunning)
    || ['已提醒', '催收中', '已升级'].includes(row?.dunning_status)
}

export function compareReceivablePriority(a, b, alertIds) {
  return Number(alertIds.has(b.contract_id)) - Number(alertIds.has(a.contract_id))
    || Number(isReceivableOverdue(b)) - Number(isReceivableOverdue(a))
    || Number(b.overdue_balance || 0) - Number(a.overdue_balance || 0)
    || Number(Boolean(b.follow_up_due)) - Number(Boolean(a.follow_up_due))
    || Number(hasReceivableFollowUp(b)) - Number(hasReceivableFollowUp(a))
    || Number(hasReceivablePendingInvoice(b)) - Number(hasReceivablePendingInvoice(a))
    || Number(b.balance || 0) - Number(a.balance || 0)
    || Number(b.aging_days || 0) - Number(a.aging_days || 0)
}
