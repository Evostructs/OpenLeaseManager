const STRUCTURE_LABELS = Object.freeze({
  multi_tower_with_commercial: '多塔楼与商业配套',
  single_building: '标准单栋楼',
  main_building_with_podium: '主楼与裙楼',
  whole_building: '整楼',
  unknown: '结构待核验'
})

const GRANULARITY_LABELS = Object.freeze({
  building: '整楼',
  zone: '分区',
  floor: '楼层',
  leasable_unit: '出租单元'
})

function normalizedText(value) {
  return value === undefined || value === null ? '' : String(value).trim()
}

function objectValue(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function list(value) {
  return Array.isArray(value) ? value : []
}

export function buildBuildingOperationsProfileView(profile = {}) {
  const source = objectValue(profile)
  const brand = objectValue(source.brand)
  const structure = objectValue(source.space_structure)
  const zones = list(structure.zones)
    .map((item) => objectValue(item))
    .map((item) => ({
      code: normalizedText(item.code),
      name: normalizedText(item.name),
      kind: normalizedText(item.kind)
    }))
    .filter((item) => item.code && item.name)
  const zoneNames = new Map(zones.map((item) => [item.code, item.name]))
  const groups = list(source.dashboard_groups)
    .map((item) => objectValue(item))
    .map((item) => {
      const available = item.available === true
      const groupZones = list(item.zone_codes)
        .map(normalizedText)
        .filter(Boolean)
      return {
        code: normalizedText(item.code),
        name: normalizedText(item.name),
        purpose: normalizedText(item.purpose),
        available,
        statusLabel: available ? '口径已启用' : '--',
        note: available
          ? normalizedText(item.purpose) || '按已发布口径展示'
          : normalizedText(item.unavailable_reason) || '当前口径尚不可用',
        zoneLabel: groupZones.length
          ? groupZones.map((code) => zoneNames.get(code) || code).join(' + ')
          : '整楼范围'
      }
    })
    .filter((item) => item.code && item.name)

  const structureType = normalizedText(structure.type) || 'unknown'
  const granularity = list(source.data_granularity)
    .map(normalizedText)
    .filter(Boolean)
  const focus = list(brand.operating_focus)
    .map(normalizedText)
    .filter(Boolean)
  const detailUnavailableReason = normalizedText(structure.detail_unavailable_reason)

  return {
    configured: Boolean(normalizedText(source.profile_version) && normalizedText(source.building_code)),
    code: normalizedText(source.building_code),
    name: normalizedText(source.building_name),
    positioning: normalizedText(brand.positioning) || '楼宇运营',
    structureType,
    structureLabel: STRUCTURE_LABELS[structureType] || '结构待核验',
    structureDetail: zones.length
      ? zones.map((item) => item.name).join(' / ')
      : detailUnavailableReason || '当前仅按整楼范围管理',
    detailUnavailableReason,
    zones,
    granularity,
    granularityLabel: granularity.length
      ? granularity.map((item) => GRANULARITY_LABELS[item] || item).join('、')
      : '粒度待核验',
    focus,
    groups,
    primaryGroup: groups.find((item) => item.available) || groups[0] || null,
    hasUnavailableGroups: groups.some((item) => !item.available),
    unavailableGroupSafety: groups.some((item) => !item.available)
      ? '暂无分区数据，以“--”显示。'
      : ''
  }
}
