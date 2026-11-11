import assert from 'node:assert/strict'
import test from 'node:test'
import { buildingAnalyticsSnapshot, createCockpitResourceCache, hasAnalyticsRoomAreas } from '../src/lib/cockpitData.js'

test('rooms share an in-flight read and an empty response is cached until expiry', async () => {
  let now = 0
  let calls = 0
  let finish
  const cache = createCockpitResourceCache({ now: () => now, ttlMs: 30 })
  const load = () => { calls += 1; return new Promise(resolve => { finish = resolve }) }
  const first = cache.read('user:version:A', load)
  const second = cache.read('user:version:A', load)
  assert.equal(first, second)
  await Promise.resolve()
  finish([])
  assert.deepEqual(await first, [])
  assert.deepEqual(await cache.read('user:version:A', load), [])
  assert.equal(calls, 1)
  now = 31
  const next = cache.read('user:version:A', load)
  await Promise.resolve()
  assert.equal(calls, 2)
  finish(['new'])
  assert.deepEqual(await next, ['new'])
})

test('clearing during a request prevents old authorization data from repopulating the cache', async () => {
  let finish
  const cache = createCockpitResourceCache()
  const old = cache.read('A', () => new Promise(resolve => { finish = resolve }))
  await Promise.resolve()
  cache.clear()
  assert.deepEqual(await cache.read('A', () => ['new grant']), ['new grant'])
  finish(['old grant'])
  await old
  assert.deepEqual(await cache.read('A', () => ['should not load']), ['new grant'])
  assert.deepEqual(await cache.read('B', () => ['other building']), ['other building'])
})

test('failed requests can retry and the cockpit keeps only the bounded number of buildings', async () => {
  const cache = createCockpitResourceCache({ maxEntries: 2 })
  await assert.rejects(cache.read('A', () => { throw new Error('network') }))
  assert.equal(await cache.read('A', () => 1), 1)
  await cache.read('B', () => 2)
  await cache.read('C', () => 3)
  assert.equal(await cache.read('A', () => 4), 4)
})

test('analytics reuses only a complete matching building snapshot', () => {
  const snapshot = { scope: { scope_type: 'building', scope_code: 'A' }, legacy_contract_stats: {}, legacy_operations: { current_month: { receivable: 10 } }, presentation: { trend: [] } }
  assert.equal(buildingAnalyticsSnapshot(snapshot, 'a').operations, snapshot.legacy_operations)
  assert.equal(buildingAnalyticsSnapshot(snapshot, 'B'), null)
  assert.equal(buildingAnalyticsSnapshot({ ...snapshot, scope: { scope_type: 'portfolio' } }), null)
  assert.equal(buildingAnalyticsSnapshot({ ...snapshot, presentation: {} }), null)
  assert.equal(hasAnalyticsRoomAreas({ tower_data: { occupied: { room_area: 100 }, vacant: { room_area: 0 } } }), true)
  assert.equal(hasAnalyticsRoomAreas({ tower_data: {} }), true)
  assert.equal(hasAnalyticsRoomAreas({ tower_data: { unknown: {} } }), false)
  assert.equal(hasAnalyticsRoomAreas({ tower_data: { unknown: { room_area: null } } }), false)
})
