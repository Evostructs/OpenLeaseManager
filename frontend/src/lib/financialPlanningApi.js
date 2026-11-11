import { api } from './api.js'

export function createFinancialPlanningApi(buildingCode, request = api) {
  if (typeof buildingCode !== 'string' || !buildingCode.trim()) throw new Error('请选择具有明细权限的项目。')
  const building = buildingCode.trim()
  const call = (path, { query = {}, body, method = 'GET' } = {}) => {
    const params = new URLSearchParams({ building })
    for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== null) params.set(key, String(value))
    return request(`/api/financial-planning${path}?${params}`, {
      method, includeScope: false, ...(body === undefined ? {} : { body: JSON.stringify(body) })
    })
  }
  return {
    source: (year, cutoff, { forecastMode, includeLatestSnapshot } = {}) => call('/source', { query: { year, closed_through_month: cutoff, forecast_mode: forecastMode, include_latest_snapshot: includeLatestSnapshot } }),
    preview: (body) => call('/preview', { method: 'POST', body }),
    versions: (year) => call('/versions', { query: { year } }),
    version: (id) => call(`/versions/${Number(id)}`),
    saveVersion: (body) => call('/versions', { method: 'POST', body }),
    adoptVersion: (id, revision) => call(`/versions/${Number(id)}/adopt`, { method: 'POST', body: { expected_revision: revision } }),
    compare: (body, { includeBaseline } = {}) => call('/compare', { method: 'POST', body, query: { include_baseline: includeBaseline } }),
    saveExpense: (body, id = null) => call(id ? `/expenses/${Number(id)}` : '/expenses', { method: id ? 'PUT' : 'POST', body })
  }
}
