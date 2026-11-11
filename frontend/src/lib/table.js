export function normalizeTableValue(value) {
  if (value == null) return ''
  if (typeof value === 'number') return value
  return String(value).trim()
}

export function includesFilter(value, query) {
  if (!query) return true
  const normalizedValue = String(normalizeTableValue(value)).toLowerCase()
  return normalizedValue.includes(String(query).trim().toLowerCase())
}

export function compareTableValues(left, right) {
  const a = normalizeTableValue(left)
  const b = normalizeTableValue(right)

  const aNumber = Number(a)
  const bNumber = Number(b)
  const bothNumeric = !Number.isNaN(aNumber) && !Number.isNaN(bNumber) && a !== '' && b !== ''
  if (bothNumeric) return aNumber - bNumber

  return String(a).localeCompare(String(b), 'zh-CN', { numeric: true, sensitivity: 'base' })
}

export function sortTableRows(rows, getter, direction) {
  if (!direction) return rows
  const factor = direction === 'desc' ? -1 : 1
  return [...rows].sort((left, right) => factor * compareTableValues(getter(left), getter(right)))
}

export function listTotalPages(total, pageSize) {
  return pageSize === 0 ? 1 : Math.max(1, Math.ceil(total / pageSize))
}

export function pageRows(rows, pager) {
  if (pager.pageSize === 0) return rows
  const start = (pager.page - 1) * pager.pageSize
  return rows.slice(start, start + pager.pageSize)
}

export function pageLabel(pager, totalPages) {
  return pager.pageSize === 0 ? '全部' : `${pager.page} / ${totalPages}`
}

export function showingText(total, pager) {
  if (pager.pageSize === 0) return `显示全部 ${total} 条`
  const start = total ? (pager.page - 1) * pager.pageSize + 1 : 0
  const end = Math.min(pager.page * pager.pageSize, total)
  return `显示 ${start}-${end} 条`
}

export function clampPager(pager, totalPages) {
  pager.page = Math.min(Math.max(1, pager.page), totalPages)
}
