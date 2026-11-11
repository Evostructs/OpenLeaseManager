import assert from 'node:assert/strict'

import {
  getFloorZone,
  getPhysicalTower,
  getPossibleTowerFloorZones,
  leasingSegment,
  normalizeRoom,
  parseFloorNumber,
  roomPriorityScore,
  roomSignalBadges
} from '../src/lib/roomsDomain.js'

assert.equal(parseFloorNumber('B2'), -2)
assert.equal(parseFloorNumber('12'), 12)
assert.equal(leasingSegment({ tower: '南塔', floor: '2', room_no: 'S0201', room_type: '办公' }), '商业配套')
assert.equal(leasingSegment({ tower: '', floor: '8', room_no: 'N0801', room_type: '办公' }), '北塔办公')
assert.deepEqual(getPossibleTowerFloorZones('南塔', 3), ['retail', 'low'])
assert.equal(getPhysicalTower({ tower: '', room_no: 'P0201' }), '南塔')
assert.equal(getFloorZone({ tower: '南塔', floor: '3', room_no: 'S0301', room_type: '餐饮' }), 'retail')

const room = normalizeRoom({ room_no: ' S0301 ', tower: '南塔', signals: { overdue: true, missing_attachment: true } })
assert.equal(room.room_no, 'S0301')
assert.deepEqual(roomSignalBadges(room), [
  { key: 'rented', label: '租', tone: 'success' },
  { key: 'overdue', label: '逾', tone: 'error' },
  { key: 'missing', label: '缺', tone: 'warning' }
])
assert.equal(roomPriorityScore({ signals: { overdue: true }, occupied: true, contract: { days_to_expiry: 30 } }), 560)
