import test from 'node:test'
import assert from 'node:assert/strict'
import { cockpitTourStops, nextCockpitTourStop } from '../src/lib/cockpitTour.js'

const grant = (code, adapter = 'live_detail', tabs = ['analytics', 'rooms']) => ({ code, operations_profile: { data_adapter: adapter, available_tabs: tabs } })
const user = items => ({ role: 'editor', scope: { items } })
const building = code => ({ scope: { scope_type: 'building', scope_code: code } })
const contribution = (code, enabled = true) => ({ building: { code, name: code }, publication_status: 'published', operating_gate: { presentation_enabled: enabled } })
const portfolio = contributions => ({ scope: { scope_type: 'portfolio' }, presentation: { contributions } })

test('single building advances immediately and visits every permitted display topic', () => {
  const stops = cockpitTourStops(building('A'), user([grant('A')]))
  let current = { buildingCode: 'A', topic: 'overview' }
  const visited = []
  for (let index = 0; index < 4; index += 1) {
    current = nextCockpitTourStop(stops, current)
    visited.push(current.topic)
  }
  assert.deepEqual(visited, ['rent', 'operations', 'space', 'overview'])
})

test('two buildings each receive the full display sequence without parity locking', () => {
  const stops = cockpitTourStops(portfolio([contribution('A'), contribution('B')]), user([grant('A'), grant('B')]))
  assert.deepEqual(stops.map(stop => `${stop.buildingCode}:${stop.topic}`), [
    ':overview', 'A:overview', 'A:rent', 'A:operations', 'A:space',
    'B:overview', 'B:rent', 'B:operations', 'B:space'
  ])
  assert.deepEqual(nextCockpitTourStop(stops, stops.at(-1)), stops[0])
})

test('summary-only grants and snapshot sources never produce detail or editing stops', () => {
  const stops = cockpitTourStops(portfolio([contribution('A'), contribution('B')]), user([grant('A', 'snapshot')]))
  assert.deepEqual(stops.map(stop => stop.topic), ['overview', 'overview', 'overview'])
  const single = cockpitTourStops(building('A'), user([]))
  assert.equal(single.length, 3)
  assert.ok(single.every(stop => stop.topic === 'overview'))
  assert.equal(nextCockpitTourStop(single, { buildingCode: 'A', topic: 'overview', metric: 'occupancy.area_rate' }).metric, 'rent.effective_daily')
})

test('tour respects presentation gates and the separate room grant', () => {
  const stops = cockpitTourStops(portfolio([contribution('A'), contribution('B', false)]), user([grant('A', 'live_detail', ['analytics']), grant('B')]))
  assert.ok(stops.every(stop => stop.buildingCode !== 'B'))
  assert.deepEqual(stops.filter(stop => stop.buildingCode === 'A').map(stop => stop.topic), ['overview', 'rent', 'operations'])
  assert.deepEqual(cockpitTourStops({}, user([])), [])
  assert.equal(nextCockpitTourStop([], {}), null)
})

test('starting from an editor or a removed stop selects a permitted view in the same building', () => {
  const stops = cockpitTourStops(portfolio([contribution('A'), contribution('B')]), user([grant('A'), grant('B')]))
  assert.deepEqual(nextCockpitTourStop(stops, { buildingCode: 'B', topic: 'decision' }), { buildingCode: 'B', topic: 'overview' })
  assert.deepEqual(nextCockpitTourStop(stops, { buildingCode: 'removed', topic: 'rent' }), stops[0])
})
