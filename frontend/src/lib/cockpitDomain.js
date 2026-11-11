import { allowedTabsForWorkspace, WORKSPACE_OPERATIONS } from './navigationPolicy.js'
import { buildLeadershipOverviewBuildingCards } from './presentationDomain.js'

const text = value => String(value ?? '').trim()
const list = value => Array.isArray(value) ? value : []

export function cockpitBuildingPath(path, code) {
  if (!text(code)) throw new Error('请先选择楼宇')
  const url = new URL(path, 'http://lease-manager.local')
  if (!url.pathname.startsWith('/api/')) throw new Error('无效的数据路径')
  url.searchParams.delete('portfolio')
  url.searchParams.set('building', text(code))
  return `${url.pathname}${url.search}`
}

export function cockpitBuildings(snapshot = {}) {
  if (snapshot?.scope?.scope_type === 'portfolio') {
    const presented = new Set(list(snapshot?.presentation?.contributions).map(row => text(row?.building?.code)))
    return buildLeadershipOverviewBuildingCards(snapshot, { allowBusinessNavigation: false })
      .map(row => ({ ...row, selectable: presented.has(row.code) && row.presentationEnabled !== false }))
  }
  const building = snapshot?.scope?.building || {}
  const code = text(building.code || snapshot?.scope?.scope_code)
  return code ? [{ code, name: text(building.name || snapshot?.scope?.display_name || code), selectable: true, metrics: [] }] : []
}

export function cockpitDetailAccess(user = {}, buildingCode = '') {
  const code = text(buildingCode)
  const building = list(user?.scope?.items).find(row => text(row.code).toLowerCase() === code.toLowerCase())
  if (!building || !code) return { allowed: false, tabs: [], profile: {} }
  const profile = building.operations_profile || {}
  const tabs = allowedTabsForWorkspace(user, WORKSPACE_OPERATIONS, { type: 'building', code: building.code })
  const allowed = profile.data_adapter === 'live_detail' && tabs.includes('analytics')
  return { allowed, tabs, profile }
}

export function cockpitMetric(snapshot, code) {
  const entry = snapshot?.metrics?.[code]
  const value = entry?.value
  if (entry?.unavailable_reason || value === null || value === undefined || value === '') return null
  return Number.isFinite(Number(value)) ? Number(value) : null
}

export function cockpitComparison(snapshot, code) {
  const available = new Set(cockpitBuildings(snapshot).filter(row => row.selectable).map(row => row.code))
  return list(snapshot?.presentation?.contributions)
    .filter(row => available.has(text(row?.building?.code)))
    .map(row => ({ code: row.building.code, name: row.building.name || row.building.code, value: cockpitMetric(row, code) }))
}

export function cockpitTrend(snapshot) {
  return list(snapshot?.presentation?.trend).map(row => {
    const number = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value)
    return { month: text(row.month || row.date).slice(0, 7), receivable: number(row.receivable), received: number(row.received), rate: number(row.received_rate) }
  }).filter(row => /^\d{4}-\d{2}$/.test(row.month)).sort((a, b) => a.month.localeCompare(b.month))
}
