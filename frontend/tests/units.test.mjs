import test from 'node:test'
import assert from 'node:assert/strict'
import { convertUnitPayload, SQM_PER_SQFT, canonicalAreaQuery, areaLabel } from '../src/i18n/units.js'

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`)

test('square feet change area and inverse unit price while preserving rent and roundtrip inputs', () => {
  const original = { id: 11, area: 100.23, unit_price: 5.56, monthly_rent: 16951.23, property_fee_rate: 2.7, notes: '100㎡ CNY', room_items: [{ area: 100.23, unit_price: 5.56 }] }
  const shown = convertUnitPayload(original, { area: 'ft2' })
  near(shown.area, 100.23 / SQM_PER_SQFT)
  near(shown.unit_price, 5.56 * SQM_PER_SQFT)
  near(shown.area * shown.unit_price, original.area * original.unit_price)
  assert.equal(shown.monthly_rent, original.monthly_rent)
  assert.deepEqual(convertUnitPayload(shown, { area: 'ft2', direction: 'input' }), original)
  assert.equal(original.area, 100.23)
})

test('FX affects monetary measures, not counts, percentages, ids, dates or user text', () => {
  const original = { total: 17, cash_total: 4, bank_total: 9, id: 49, area: 100, unit_price: 6, rent: 1000, received_rate: 52.4, month: 11, notes: '1000 CNY', adjustment_type: 'percent', adjustment_value: 10, rows: [{ rent: 1000, property_fee: 200, total: 1200 }], rent_change_percent: 5 }
  const value = convertUnitPayload(original, { area: 'ft2', rate: 0.15 })
  assert.equal(value.rent, 150)
  assert.equal(value.rows[0].total, 180)
  for (const key of ['total', 'cash_total', 'bank_total', 'id', 'received_rate', 'month', 'notes', 'adjustment_value', 'rent_change_percent']) assert.equal(value[key], original[key])
  near(value.area * value.unit_price, original.area * original.unit_price * 0.15)
})

test('dashboard, portfolio metrics and rent timelines retain dimensional consistency', () => {
  const payload = { cards: [{ key: 'unpaid_balance', value: '1,000', raw_value: 1000, unit: '元' }], metric: { code: 'rent.effective_daily', value: 5, numerator: 500, denominator: 100, unit: 'CNY/sqm/day' }, tower: [{ total: 150, leased: 100, rate: 66.7 }], price: { current_by_segment: { overall: 5 }, trend_by_segment: { office: [4, 5, null] } }, daily: [{ area: 100, area_days: 3000, effective_daily_rent: 5, rent: 500 }] }
  const shown = convertUnitPayload(payload, { area: 'ft2', rate: 0.2 })
  assert.equal(shown.cards[0].value, '200')
  near(shown.metric.numerator / shown.metric.denominator, shown.metric.value)
  near(shown.tower[0].total, 150 / SQM_PER_SQFT)
  assert.equal(shown.tower[0].rate, 66.7)
  near(shown.price.current_by_segment.overall, SQM_PER_SQFT)
  near(shown.price.trend_by_segment.office[1], SQM_PER_SQFT)
  assert.equal(shown.price.trend_by_segment.office[2], null)
  near(shown.daily[0].area * shown.daily[0].effective_daily_rent, shown.daily[0].rent)
})

test('area filtering and labels use the same selected dimension', () => {
  assert.equal(canonicalAreaQuery('/api/rooms?min_area=100&keyword=5', 'ft2'), '/api/rooms?min_area=9.290304&keyword=5')
  assert.equal(areaLabel('CNY/㎡/天 · m² · sqm', 'ft2'), 'CNY/ft²/天 · ft² · sqft')
})
