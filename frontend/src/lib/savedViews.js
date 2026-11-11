// Only view preferences are persisted. Business rows and authentication are never stored here.
const fields = new Set(['keyword', 'tower', 'status', 'sortBy', 'sortDir', 'month', 'contractNo', 'tenantName', 'roomNo', 'invoiceStatus', 'dunningOwner', 'dunningStatus', 'overdueOnly', 'ledger'])
export function cleanViewFilters(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return {}
  return Object.fromEntries(Object.entries(input).filter(([key, value]) => fields.has(key) && typeof value === 'string' && value.length <= 160))
}
export function viewStorageKey(user, scope, page) {
  if (!user?.id || !scope?.type || !scope?.code) return ''
  return `lease-views:v1:${JSON.stringify([user.id, scope.type, scope.code, page])}`
}
export function readSavedViews(storage, key) {
  if (!key) return []
  try {
    const rows = JSON.parse(storage.getItem(key) || '[]')
    return Array.isArray(rows) ? rows.slice(0, 8).filter(row => typeof row?.name === 'string' && row.name.length <= 40)
      .map(row => ({ name: row.name, filters: cleanViewFilters(row.filters) })) : []
  } catch { return [] }
}
export function saveView(storage, key, name, filters) {
  name = String(name || '').trim().slice(0, 40)
  if (!key || !name) throw new Error('请先填写视图名称')
  const rows = readSavedViews(storage, key).filter(row => row.name !== name)
  if (rows.length >= 8) throw new Error('最多保存 8 个常用视图')
  const result = [...rows, { name, filters: cleanViewFilters(filters) }]
  storage.setItem(key, JSON.stringify(result))
  return result
}
export function buildViewLink(origin, page, scope, filters, detail) {
  const paths = { contracts: '/contracts', receivables: '/receivables', customers: '/customers', rooms: '/rooms' }
  if (!paths[page] || !scope?.code || !['building', 'portfolio'].includes(scope.type)) return ''
  const url = new URL(paths[page], origin)
  url.searchParams.set(scope.type, scope.code)
  if (Object.keys(cleanViewFilters(filters)).length) url.searchParams.set('view', JSON.stringify(cleanViewFilters(filters)))
  if (['contract', 'customer'].includes(detail?.type) && Number.isSafeInteger(Number(detail.id)) && Number(detail.id) > 0) {
    url.searchParams.set('detail', detail.type); url.searchParams.set('id', String(detail.id))
  }
  return url.href
}
export function readViewLink(query) {
  let filters = {}
  try { if (typeof query.view === 'string' && query.view.length <= 2400) filters = cleanViewFilters(JSON.parse(query.view)) } catch {}
  const id = Number(query.id)
  return { filters, detail: ['contract', 'customer'].includes(query.detail) && Number.isSafeInteger(id) && id > 0 ? { type: query.detail, id } : null }
}

export function navigationViewQuery(query, currentPath, nextPath, scope) {
  const next = { ...query }
  if (currentPath !== nextPath || (query.building && (scope.type !== 'building' || query.building !== scope.code)) || (query.portfolio && (scope.type !== 'portfolio' || query.portfolio !== scope.code))) {
    delete next.view; delete next.detail; delete next.id
  }
  return next
}
