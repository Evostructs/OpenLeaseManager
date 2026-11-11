import { formatCurrencyFixed } from './format.js'

export function numericValue(value) {
  const numeric = Number(value || 0)
  return Number.isFinite(numeric) ? numeric : 0
}

export function roundTo(value, digits = 1) {
  const factor = 10 ** digits
  return Math.round(numericValue(value) * factor) / factor
}

export function shareValue(value, total) {
  return total > 0 ? roundTo((numericValue(value) / total) * 100, 1) : 0
}

export function customerValueTier(row, rank) {
  if (numericValue(row.rent) > 0 && (rank <= 3 || numericValue(row.rentShare) >= 20)) return 'high'
  if (numericValue(row.rent) > 0) return 'medium'
  return 'low'
}

export function customerRiskTier(row) {
  if (numericValue(row.overdueBalance) > 0 || numericValue(row.followUpOverdueDays) > 0) return 'high'
  if (row.hasFollowUp || row.attachmentGap || row.profileGap) return 'medium'
  return 'low'
}

export function customerRiskScore(row) {
  let score = Math.min(14, numericValue(row.rentShare) / 2)
  if (numericValue(row.overdueBalance) > 0) score += 60 + Math.min(20, numericValue(row.maxAgingDays) / 3)
  if (numericValue(row.followUpOverdueDays) > 0) score += 36 + Math.min(12, numericValue(row.followUpOverdueDays))
  else if (row.hasFollowUp) score += 22
  if (row.attachmentGap) score += 16
  if (row.profileGap) score += 8
  return roundTo(score, 1)
}

export function compactCurrency(value) {
  const numeric = numericValue(value)
  if (Math.abs(numeric) >= 100000000) return `${(numeric / 100000000).toFixed(1)}亿`
  if (Math.abs(numeric) >= 10000) return `${(numeric / 10000).toFixed(1)}万`
  return formatCurrencyFixed(numeric)
}

export function customerRiskSignals(row) {
  const signals = []
  if (numericValue(row.overdueBalance) > 0) signals.push(`应收 ${compactCurrency(row.overdueBalance)}`)
  if (row.followUpLabel) signals.push(row.followUpLabel)
  if (row.attachmentGap) signals.push(`缺${row.attachmentGap}`)
  if (row.profileGap) signals.push('资料待补')
  return signals.length ? signals : ['暂无运营提示']
}

export function customerSignalKey(customerId, name = '') {
  return customerId ? `id:${Number(customerId)}` : `name:${String(name || '').trim()}`
}

export function rowCustomerName(row) {
  return row?.tenant_name || row?.customer_name || row?.name || row?.title || '--'
}

export function roomsText(row, limit = 3) {
  const rooms = Array.isArray(row?.rooms) ? row.rooms : []
  const count = numericValue(row?.room_count || rooms.length)
  if (!rooms.length) return count ? `${count} 间` : '--'
  const shown = rooms.slice(0, limit).join('、')
  return count > limit ? `${shown} 等 ${count} 间` : shown
}
