export function normalizeText(value) {
  return String(value ?? '').trim()
}

function objectValue(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function listValue(value) {
  return Array.isArray(value) ? value : []
}

function normalizedProfileZones(profile) {
  return listValue(objectValue(profile).space_structure?.zones)
    .map((item) => objectValue(item))
    .map((item) => ({
      code: normalizeText(item.code),
      value: normalizeText(item.name),
      label: normalizeText(item.name),
      kind: normalizeText(item.kind)
    }))
    .filter((item) => item.code && item.value)
}

export function profileAnalysisSegments(profile = {}) {
  const source = objectValue(profile)
  const zones = normalizedProfileZones(source)
  const zoneByCode = new Map(zones.map((item) => [item.code, item]))
  const configuredGroups = listValue(source.analysis_groups)
    .map((item) => objectValue(item))
    .filter((item) => item.available !== false)
    .map((item) => ({
      code: normalizeText(item.code),
      label: normalizeText(item.name),
      zoneCodes: listValue(item.zone_codes).map(normalizeText).filter(Boolean)
    }))
    .filter((item) => item.code && item.label)
  const onlyWholeBuildingGroup = configuredGroups.length === 1
    && configuredGroups[0].code === 'WHOLE_BUILDING'
  const onlyWholeBuildingZone = zones.length === 1 && zones[0].code === 'WHOLE_BUILDING'
  const groups = onlyWholeBuildingGroup && !onlyWholeBuildingZone
    ? []
    : configuredGroups.length > 1
      ? configuredGroups.filter((item) => item.code !== 'WHOLE_BUILDING')
      : configuredGroups

  if (!groups.length) {
    return zones.map((zone) => ({
      code: zone.code,
      label: zone.label,
      filterValue: zone.value,
      zoneCodes: [zone.code],
      aliases: [zone.code, zone.value]
    }))
  }

  return groups.map((group) => {
    const groupZones = group.zoneCodes.map((code) => zoneByCode.get(code)).filter(Boolean)
    const sameCodeZone = zoneByCode.get(group.code)
    const filterValue = groupZones.length === 1
      ? groupZones[0].value
      : sameCodeZone?.value || group.label
    return {
      ...group,
      filterValue,
      aliases: [...new Set([
        group.code,
        group.label,
        filterValue,
        ...groupZones.map((item) => item.value)
      ].filter(Boolean))]
    }
  })
}

export function profileSpaceOptions(profile = {}) {
  const zones = normalizedProfileZones(profile)
  if (zones.length) return zones
  return profileAnalysisSegments(profile).map((segment) => ({
    code: segment.code,
    value: segment.filterValue,
    label: segment.label,
    kind: ''
  }))
}

export function usesLegacyDemoAFloorPlan(profile = {}) {
  const source = objectValue(profile)
  const zoneCodes = new Set(normalizedProfileZones(source).map((item) => item.code))
  const granularity = new Set(listValue(source.data_granularity).map(normalizeText))
  return source.space_structure?.type === 'multi_tower_with_commercial'
    && granularity.has('floor')
    && ['SOUTH_OFFICE', 'NORTH_OFFICE', 'COMMERCIAL_SUPPORT'].every((code) => zoneCodes.has(code))
}

export function roomAnalysisSegment(room, profile = {}) {
  const segments = profileAnalysisSegments(profile)
  if (!segments.length) return null
  if (usesLegacyDemoAFloorPlan(profile)) {
    const label = leasingSegment(room)
    return segments.find((item) => item.label === label || item.aliases.includes(label)) || null
  }
  const tower = normalizeText(room?.tower)
  const matched = segments.find((item) => item.aliases.includes(tower))
  if (matched) return matched
  return segments.length === 1 && segments[0].code === 'WHOLE_BUILDING' ? segments[0] : null
}

export function normalizeRoom(room) {
  return {
    ...room,
    tower: normalizeText(room.tower),
    floor: normalizeText(room.floor),
    room_no: normalizeText(room.room_no),
    room_type: normalizeText(room.room_type),
    notes: normalizeText(room.notes),
    contract: room.contract || null,
    signals: room.signals || {},
    operating_statuses: room.operating_statuses || []
  }
}

export function parseFloorNumber(value) {
  const raw = normalizeText(value).toUpperCase()
  if (!raw) return null
  if (raw.startsWith('B')) return -Number(raw.slice(1) || 0)
  const number = Number(raw)
  return Number.isNaN(number) ? null : number
}

export function normalizeHeatmapFloor(value) {
  const parsed = parseFloorNumber(value)
  return parsed == null ? normalizeText(value || '0') : String(parsed)
}

export function getUseCategory(room) {
  return room.room_type === '办公' ? 'office' : 'retail'
}

export function isCommercialRoom(room) {
  return leasingSegment(room) === '商业配套'
}

export function getRoomStatusTone(status) {
  if (status === '空置') return 'info'
  if (status === '即将到期' || status === '逾期占用' || status === '资料缺失') return 'warning'
  if (status === '历史逾期') return 'error'
  if (status === '已退租' || status === '已到期') return 'error'
  return 'success'
}

export function segmentTone(segment) {
  if (segment.pressureCount > 0) return 'warning'
  if (segment.occupancyRate >= 90) return 'success'
  if (segment.occupancyRate >= 75) return 'info'
  return 'warning'
}

export function roomMonthlyRent(room) {
  return Number(room.contract?.monthly_rent || 0)
}

export function roomMatchesPrioritySignal(room) {
  return Boolean(
    room.signals?.overdue ||
    room.signals?.expiring_90 ||
    room.signals?.missing_attachment ||
    !room.occupied ||
    room.signals?.vacant ||
    room.contract?.conflict_count > 1
  )
}

export function roomHasBlockedSignal(room) {
  return Boolean(
    room.contract?.conflict_count > 1 ||
    (room.operating_statuses || []).includes('逾期占用')
  )
}

export function roomSignalBadges(room) {
  const signals = room.signals || {}
  if (signals.vacant) return [{ key: 'vacant', label: '空', tone: 'info' }]
  const badges = [{ key: 'rented', label: '租', tone: 'success' }]
  if (signals.expiring_90) badges.push({ key: 'expiring', label: '到', tone: 'warning' })
  if (signals.overdue) badges.push({ key: 'overdue', label: '逾', tone: 'error' })
  if (signals.missing_attachment) badges.push({ key: 'missing', label: '缺', tone: 'warning' })
  return badges
}

export function roomHeatmapTestId(room) {
  const value = normalizeText(room.room_no || room.id).replace(/\s+/g, '-')
  return `rooms-heatmap-room-${value || 'unknown'}`
}

export function displayTowerLabel(value) {
  const tower = normalizeText(value)
  if (tower === '南塔') return '南塔办公'
  if (tower === '北塔') return '北塔办公'
  return tower || '未标注'
}

export function leasingSegment(room) {
  const tower = normalizeText(room?.tower)
  const floor = parseFloorNumber(room?.floor)
  const roomNo = normalizeText(room?.room_no).toUpperCase()

  if (tower === '商业配套' || getUseCategory(room || {}) === 'retail') return '商业配套'
  if ((tower === '南塔' || tower === '北塔') && floor != null && floor >= 1 && floor <= 2) return '商业配套'
  if (tower === '南塔' || tower === '南塔办公') return '南塔办公'
  if (tower === '北塔' || tower === '北塔办公') return '北塔办公'
  if (roomNo.startsWith('P')) return '商业配套'
  if (roomNo.startsWith('S')) return '南塔办公'
  if (roomNo.startsWith('N')) return '北塔办公'
  return displayTowerLabel(tower)
}

export function getBusinessSubtype(room) {
  if (room.room_type === '餐饮') return 'dining'
  if (room.room_type === '健身') return 'fitness'
  if (room.room_type === '便利店') return 'convenience'
  if (room.room_type && room.room_type !== '办公') return 'other'
  return ''
}

export function getTowerFloorZone(tower, floor) {
  if (tower === '南塔') {
    if (floor != null && floor >= 3 && floor <= 12) return 'low'
    if (floor != null && floor >= 16 && floor <= 25) return 'mid'
    if (floor != null && floor >= 26 && floor <= 36) return 'high'
  }

  if (tower === '北塔') {
    if (floor != null && floor >= 3 && floor <= 10) return 'low'
    if (floor != null && floor >= 11 && floor <= 22) return 'high'
  }

  return 'other'
}

export function getPossibleTowerFloorZones(tower, floor) {
  if (tower === '南塔') {
    if (floor === 3) return ['retail', 'low']
    if (floor != null && floor >= 1 && floor <= 2) return ['retail']
  }

  if (tower === '北塔' && floor != null && floor >= 1 && floor <= 2) return ['retail']

  return [getTowerFloorZone(tower, floor)]
}

export function getPhysicalTower(room) {
  if (room.tower === '北塔' || room.tower === '南塔') return room.tower
  const roomNo = normalizeText(room.room_no).toUpperCase()
  if (roomNo.startsWith('N')) return '北塔'
  if (roomNo.startsWith('S') || roomNo.startsWith('P')) return '南塔'
  return room.tower === '商业配套' ? '南塔' : room.tower || '未标注塔楼'
}

export function getFloorZone(room) {
  const floor = parseFloorNumber(room.floor)
  const tower = getPhysicalTower(room)

  if (floor != null && floor < 0) return 'basement'
  if (isCommercialRoom(room) && getPossibleTowerFloorZones(tower, floor).includes('retail')) return 'retail'
  return getTowerFloorZone(tower, floor)
}

export function roomPriorityScore(room) {
  let score = 0
  if (room.signals?.overdue) score += 500
  if (room.signals?.expiring_90) score += 400
  if (room.signals?.missing_attachment) score += 300
  if (!room.occupied || room.signals?.vacant) score += 260
  if (room.contract?.conflict_count > 1) score += 120
  if (room.contract?.days_to_expiry != null) score += Math.max(0, 90 - Number(room.contract.days_to_expiry || 0))
  return score
}

export function floorOrder(value) {
  return parseFloorNumber(value) ?? -999
}
