export function dunningStatusTone(status) {
  if (status === '已升级') return 'error'
  if (['已提醒', '催收中'].includes(status)) return 'warning'
  if (status === '无需催收') return 'success'
  return 'info'
}

export function invoiceStatusTone(status) {
  if (status === '已开票') return 'success'
  if (status === '红冲/作废') return 'error'
  if (status === '待开票') return 'warning'
  if (status === '无需开票') return 'info'
  return 'warning'
}
