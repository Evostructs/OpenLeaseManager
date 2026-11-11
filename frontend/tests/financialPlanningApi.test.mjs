import assert from 'node:assert/strict'
import test from 'node:test'
import { createFinancialPlanningApi } from '../src/lib/financialPlanningApi.js'

test('every financial request uses the explicit project instead of ambient portfolio scope', async () => {
  const calls = []
  const client = createFinancialPlanningApi('BUILDING A', async (path, options) => { calls.push({ path, options }); return {} })
  await client.source(2026, 8)
  await client.preview({ year: 2026 })
  await client.saveVersion({ expected_revision: 3 })
  await client.adoptVersion(2, 4)
  await client.version(2)
  await client.versions(2026)
  await client.compare({ baseline_version_id: 2 })
  await client.saveExpense({ amount_yuan: 20000 })
  await client.saveExpense({ amount_yuan: 30000, expected_revision: 1 }, 3)
  for (const call of calls) {
    const url = new URL(call.path, 'http://localhost')
    assert.equal(url.searchParams.get('building'), 'BUILDING A')
    assert.equal(call.options.includeScope, false)
  }
  assert.deepEqual(JSON.parse(calls[3].options.body), { expected_revision: 4 })
  assert.equal(calls.at(-1).options.method, 'PUT')
})

test('an absent project cannot create a financial client or emit a request', () => {
  let called = false
  assert.throws(() => createFinancialPlanningApi('', () => { called = true }))
  assert.equal(called, false)
})

test('forecast and comparison response options are opt-in query parameters and preserve payloads', async () => {
  const calls = []
  const client = createFinancialPlanningApi('DEMO_A', async (path, options) => { calls.push({ path, options }); return {} })
  const body = { year: 2026, baseline_version_id: 2, current_version_id: 3, closed_through_month: 8 }
  await client.source(2026, 8)
  await client.compare(body)
  assert.equal(calls[0].path, '/api/financial-planning/source?building=DEMO_A&year=2026&closed_through_month=8')
  assert.equal(calls[1].path, '/api/financial-planning/compare?building=DEMO_A')
  for (const forecastMode of ['live', 'workspace', 'workspace_deferred', 'none']) await client.source(2026, 8, { forecastMode })
  await client.compare(body, { includeBaseline: false })
  await client.compare(body, { includeBaseline: true })
  assert.deepEqual(calls.slice(2, 6).map(call => new URL(call.path, 'http://localhost').searchParams.get('forecast_mode')), ['live', 'workspace', 'workspace_deferred', 'none'])
  assert.equal(new URL(calls[6].path, 'http://localhost').searchParams.get('include_baseline'), 'false')
  assert.equal(new URL(calls[7].path, 'http://localhost').searchParams.get('include_baseline'), 'true')
  for (const index of [1, 6, 7]) {
    assert.equal(calls[index].options.method, 'POST')
    assert.equal(calls[index].options.includeScope, false)
    assert.deepEqual(JSON.parse(calls[index].options.body), body)
  }
})

test('a metadata refresh can omit only the latest saved snapshot without changing scope or defaults', async () => {
  const calls = []
  const client = createFinancialPlanningApi('DEMO_A', async (path, options) => { calls.push({ path, options }); return {} })
  await client.source(2026, 8, { forecastMode: 'none', includeLatestSnapshot: false })
  await client.source(2026, 8, { forecastMode: 'workspace_deferred', includeLatestSnapshot: true })
  await client.source(2026, 8, { forecastMode: 'workspace_deferred' })
  assert.deepEqual(calls.map(call => new URL(call.path, 'http://localhost').searchParams.get('include_latest_snapshot')), ['false', 'true', null])
  for (const { path, options } of calls) {
    const params = new URL(path, 'http://localhost').searchParams
    assert.equal(params.get('building'), 'DEMO_A')
    assert.equal(params.get('year'), '2026')
    assert.equal(params.get('closed_through_month'), '8')
    assert.equal(options.method, 'GET')
    assert.equal(options.includeScope, false)
  }
})
