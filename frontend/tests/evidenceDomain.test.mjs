import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildEvidenceCoverage,
  buildEvidenceItems,
  compactEvidenceReference,
  evidenceStatusMeta,
  formatEvidenceScalar,
  formatEvidenceValue
} from '../src/lib/evidenceDomain.js'

test('evidence formatting preserves missing values, true zero and OPS_V1 percentages', () => {
  assert.equal(formatEvidenceValue(null, 'percent'), '--')
  assert.equal(formatEvidenceValue('', 'CNY'), '--')
  assert.equal(formatEvidenceValue(0, 'percent'), '0%')
  assert.equal(formatEvidenceValue(0.8, 'percent'), '0.8%')
  assert.equal(formatEvidenceValue(12860000, 'CNY'), '12,860,000.00 元')
  assert.equal(formatEvidenceScalar(0), '0')
  assert.equal(formatEvidenceScalar(null), '--')
})

test('coverage cards keep building, area, day and estimate coverage independent', () => {
  const cards = buildEvidenceCoverage({
    authorized_buildings: 5,
    eligible_buildings: 2,
    included_buildings: 1,
    covered_area_sqm: null,
    known_total_area_sqm: 31000,
    covered_days: 31,
    eligible_days: 62,
    estimate_share: 0
  })

  assert.deepEqual(cards.map((item) => item.value), ['1 / 5', '--', '31 天', '0%'])
  assert.equal(cards[0].hint, '2 栋具备数据')
  assert.equal(cards[1].hint, '可靠总面积 31,000 ㎡')
})

test('building evidence keeps excluded facts visible and missing buildings explicit', () => {
  const rows = buildEvidenceItems({
    metric: { unit: 'percent' },
    items: [
      {
        building: { id: 1, code: 'DEMO_A', name: '示例双塔中心' },
        status: 'excluded',
        included: false,
        reason: '来源业务时间早于目标统计日',
        fact: {
          value: 98.6,
          numerator: 986,
          denominator: 1000,
          freshness_status: 'stale'
        },
        source: { id: 'demo_a-detail-live', name: 'DEMO_A 现有租赁明细库' },
        limitations: ['业务时间未知']
      },
      {
        building: { id: 2, code: 'DEMO_B', name: '示例主裙楼中心' },
        status: 'missing',
        included: false,
        reason: '该统计日没有当前已发布快照',
        fact: null,
        source: null,
        limitations: []
      }
    ]
  })

  assert.equal(rows[0].status.label, '有数据未纳入')
  assert.equal(rows[0].value, '98.6%')
  assert.equal(rows[0].included, false)
  assert.deepEqual(rows[0].limitations, ['业务时间未知'])
  assert.equal(rows[0].hasEvidenceChain, true)
  assert.equal(rows[1].status.label, '缺少同日数据')
  assert.equal(rows[1].value, '--')
  assert.equal(rows[1].hasEvidenceChain, false)
})

test('single-building evidence keeps the v1 status while presenting single-building meaning', () => {
  const [row] = buildEvidenceItems({
    scope: { scope_type: 'building' },
    metric: { unit: 'percent' },
    items: [{
      building: { code: 'DEMO_B', name: '示例主裙楼中心' },
      status: 'included',
      included: true,
      reason: '当前单楼指标可用；板块合计开关不适用于单楼范围',
      fact: { value: 90 }
    }]
  })

  assert.equal(row.status.label, '单楼指标可用')
  assert.equal(row.status.tone, 'included')
  assert.match(row.reason, /板块合计开关不适用于单楼范围/)
})

test('status and reference helpers are stable for audit labels', () => {
  assert.deepEqual(evidenceStatusMeta('live_detail_limited'), { label: '实时明细受限', tone: 'limited' })
  assert.equal(compactEvidenceReference('0123456789abcdefghijklmnopqrstuvwxyz'), '0123456789…stuvwxyz')
  assert.equal(compactEvidenceReference(null), '--')
})
