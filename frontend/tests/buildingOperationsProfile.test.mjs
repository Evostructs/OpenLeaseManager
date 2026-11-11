import test from 'node:test'
import assert from 'node:assert/strict'

import { buildBuildingOperationsProfileView } from '../src/lib/buildingOperationsProfile.js'

function profile(overrides = {}) {
  return {
    profile_version: 'building-operations-profile-v1',
    building_code: 'BUILDING',
    building_name: '示例楼宇',
    brand: {
      positioning: '示例运营',
      operating_focus: ['到期续租', '收缴']
    },
    space_structure: {
      type: 'whole_building',
      zones: [],
      detail_unavailable_reason: '内部结构尚未核验'
    },
    data_granularity: ['building'],
    dashboard_groups: [
      {
        code: 'WHOLE_BUILDING',
        name: '整楼',
        zone_codes: [],
        purpose: '整栋经营情况',
        available: true,
        unavailable_reason: null
      }
    ],
    ...overrides
  }
}

test('standard single building keeps unknown floors explicit without inventing zones', () => {
  const view = buildBuildingOperationsProfileView(profile({
    space_structure: {
      type: 'single_building',
      zones: [],
      detail_unavailable_reason: '楼层数、分区和出租单元尚未核验，当前不虚构楼层或房号'
    }
  }))

  assert.equal(view.structureLabel, '标准单栋楼')
  assert.equal(view.zones.length, 0)
  assert.equal(view.granularityLabel, '整楼')
  assert.match(view.structureDetail, /不虚构楼层或房号/)
})

test('main building and podium expose separate scopes and fail closed for unavailable market view', () => {
  const view = buildBuildingOperationsProfileView(profile({
    space_structure: {
      type: 'main_building_with_podium',
      zones: [
        { code: 'MAIN_BUILDING', name: '主楼', kind: 'office' },
        { code: 'PODIUM', name: '裙楼', kind: 'mixed_use' }
      ]
    },
    dashboard_groups: [
      {
        code: 'WHOLE_BUILDING',
        name: '整栋经营',
        zone_codes: ['MAIN_BUILDING', 'PODIUM'],
        purpose: '主楼和裙楼全部纳入',
        available: true
      },
      {
        code: 'MARKET_LEASING',
        name: '市场化出租',
        zone_codes: ['MAIN_BUILDING'],
        purpose: '重点观察实际市场化出租范围',
        available: false,
        unavailable_reason: '当前已发布快照仅提供整楼粒度，分区指标尚不可用'
      }
    ]
  }))

  assert.equal(view.structureLabel, '主楼与裙楼')
  assert.equal(view.groups[0].zoneLabel, '主楼 + 裙楼')
  assert.equal(view.groups[1].zoneLabel, '主楼')
  assert.equal(view.groups[1].statusLabel, '--')
  assert.match(view.groups[1].note, /分区指标尚不可用/)
  assert.match(view.unavailableGroupSafety, /暂无分区数据/)
})

test('whole-building workbench preserves stable-operation focus without hard-coding occupancy', () => {
  const view = buildBuildingOperationsProfileView(profile())

  assert.deepEqual(view.focus, ['到期续租', '收缴'])
  assert.equal(view.structureLabel, '整楼')
  assert.equal(view.groups[0].statusLabel, '口径已启用')
  assert.equal(JSON.stringify(view).includes('满租'), false)
})
