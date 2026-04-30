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
