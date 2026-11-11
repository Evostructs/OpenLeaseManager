import test from 'node:test'
import assert from 'node:assert/strict'
import { cockpitBuildingPath, cockpitDetailAccess, cockpitComparison, cockpitMetric, cockpitTrend } from '../src/lib/cockpitDomain.js'

test('cockpit only loads detail for an explicitly granted live-detail building', () => {
  assert.equal(cockpitDetailAccess({ role: 'admin', scope: { portfolio_overview: { items: [{ code: 'P' }] } } }, 'A').allowed, false)
  const user = { role: 'editor', scope: { items: [{ code: 'A', operations_profile: { data_adapter: 'live_detail', available_tabs: ['analytics', 'rooms'] } }, { code: 'B', operations_profile: { data_adapter: 'snapshot', available_tabs: ['analytics'] } }] } }
  assert.equal(cockpitDetailAccess(user, 'A').allowed, true)
  assert.equal(cockpitDetailAccess(user, 'B').allowed, false)
  assert.equal(cockpitDetailAccess(user, 'C').allowed, false)
  assert.equal(cockpitDetailAccess({ ...user, scope: { items: [] } }, 'A').allowed, false)
})

test('explicit request retains filters and excludes conflicting portfolio scope', () => {
  assert.equal(cockpitBuildingPath('/api/rooms?portfolio=P&occupancy=vacant', 'A'), '/api/rooms?occupancy=vacant&building=A')
  assert.throws(() => cockpitBuildingPath('/api/rooms', ''))
})

test('comparison never uses raw undisplayed contributions or missing values as zero', () => {
  const contribution = code => ({ building: { code, name: code }, publication_status: 'published', operating_gate: { presentation_enabled: true }, metrics: { x: { value: 0 } } })
  const snapshot = { scope: { scope_type: 'portfolio' }, contributions: [contribution('HIDDEN')], presentation: { contributions: [contribution('A')] } }
  assert.deepEqual(cockpitComparison(snapshot, 'x'), [{ code: 'A', name: 'A', value: 0 }])
  assert.equal(cockpitMetric({ metrics: { x: { value: null } } }, 'x'), null)
  assert.equal(cockpitMetric({ metrics: { x: { value: 99, unavailable_reason: '未覆盖' } } }, 'x'), null)
})

test('trend preserves unknowns and chronological months', () => {
  const rows = cockpitTrend({ presentation: { trend: [{ month: '2026-09', receivable: 0, received: null }, { month: '2026-08', receivable: '', received: 1 }, { month: 'unknown' }] } })
  assert.equal(rows.length, 2)
  assert.equal(rows[0].receivable, null)
  assert.equal(rows[1].receivable, 0)
  assert.equal(rows[1].received, null)
})
