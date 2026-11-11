const DAY_MS = 24 * 60 * 60 * 1000

export const FALLBACK_RENT_SEGMENTS = [
  { key: 'overall', label: '整体', color: '#1d4ed8' }
]

const SEGMENT_COLORS = ['#1d4ed8', '#0f766e', '#7c3aed', '#c2410c', '#0369a1', '#be123c']

function finiteNumber(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

export function parseIsoDate(value) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const date = new Date(Date.UTC(year, month - 1, day))
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day
  ) return null
  return date
}

export function formatIsoDate(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return ''
  return [
    date.getUTCFullYear(),
    String(date.getUTCMonth() + 1).padStart(2, '0'),
    String(date.getUTCDate()).padStart(2, '0')
  ].join('-')
}

export function isoDateTimestamp(value) {
  return parseIsoDate(value)?.getTime() ?? null
}

export function addDays(value, amount) {
  const date = parseIsoDate(value)
  if (!date) return ''
  date.setUTCDate(date.getUTCDate() + Number(amount || 0))
  return formatIsoDate(date)
}

export function addYearsClamped(value, amount) {
  const date = parseIsoDate(value)
  if (!date) return ''
  const targetYear = date.getUTCFullYear() + Number(amount || 0)
  const month = date.getUTCMonth()
  const day = date.getUTCDate()
  const lastDay = new Date(Date.UTC(targetYear, month + 1, 0)).getUTCDate()
  return formatIsoDate(new Date(Date.UTC(targetYear, month, Math.min(day, lastDay))))
}

export function inclusiveDayCount(startDate, endDate) {
  const start = isoDateTimestamp(startDate)
  const end = isoDateTimestamp(endDate)
  if (start == null || end == null || end < start) return 0
  return Math.floor((end - start) / DAY_MS) + 1
}

export function clampDateRange(startDate, endDate, minDate, maxDate) {
  const min = parseIsoDate(minDate) ? minDate : startDate
  const max = parseIsoDate(maxDate) ? maxDate : endDate
  let start = parseIsoDate(startDate) ? startDate : min
  let end = parseIsoDate(endDate) ? endDate : max
  if (start < min) start = min
  if (start > max) start = max
  if (end < min) end = min
  if (end > max) end = max
  if (start > end) [start, end] = [end, start]
  return { start, end }
}

export function previousEqualRange(startDate, endDate) {
  const days = inclusiveDayCount(startDate, endDate)
  if (!days) return null
  const end = addDays(startDate, -1)
  return { start: addDays(end, -(days - 1)), end }
}

export function yearAgoRange(startDate, endDate) {
  if (!inclusiveDayCount(startDate, endDate)) return null
  return {
    start: addYearsClamped(startDate, -1),
    end: addYearsClamped(endDate, -1)
  }
}

export function chooseTimelineGranularity(startDate, endDate) {
  const days = inclusiveDayCount(startDate, endDate)
  if (days <= 184) return 'day'
  if (days <= 730) return 'week'
  return 'month'
}

export function fitRentExportImage(
  sourceWidth,
  sourceHeight,
  { minWidth = 1500, maxWidth = 2200, maxHeight = 1700 } = {}
) {
  const width = Math.max(finiteNumber(sourceWidth, 1), 1)
  const height = Math.max(finiteNumber(sourceHeight, 1), 1)
  const safeMinWidth = Math.max(finiteNumber(minWidth, 1), 1)
  const safeMaxWidth = Math.max(finiteNumber(maxWidth, safeMinWidth), safeMinWidth)
  const safeMaxHeight = Math.max(finiteNumber(maxHeight, 1), 1)
  const preferredWidth = Math.min(safeMaxWidth, Math.max(safeMinWidth, width))
  const scale = Math.min(preferredWidth / width, safeMaxHeight / height)
  return {
    width: Math.max(Math.round(width * scale), 1),
    height: Math.max(Math.round(height * scale), 1),
    scale
  }
}

export function hasFutureRentData(daily, today) {
  if (!parseIsoDate(today) || !Array.isArray(daily)) return false
  return daily.some((row) => (
    parseIsoDate(row?.date)
    && row.date > today
    && Object.values(row.segments || {}).some((value) => (
      Math.max(finiteNumber(value?.area), 0) > 0
      || finiteNumber(value?.rent) !== 0
    ))
  ))
}

function normalizedSegments(rawSegments) {
  const configured = Array.isArray(rawSegments)
    ? rawSegments.filter((item) => item && String(item.key || '').trim())
    : []
  const source = configured.length ? configured : FALLBACK_RENT_SEGMENTS
  const seen = new Set()
  return source.flatMap((item, index) => {
    const key = String(item.key || '').trim()
    if (!key || seen.has(key)) return []
    seen.add(key)
    return [{
      key,
      label: String(item.label || key),
      color: String(item.color || SEGMENT_COLORS[index % SEGMENT_COLORS.length])
    }]
  })
}

function normalizeSegmentValue(value) {
  const area = Math.max(finiteNumber(value?.area), 0)
  const rent = finiteNumber(value?.rent)
  const suppliedRate = value?.effective_daily_rent
  return {
    rent,
    area,
    // 空置日不是“零租金”；有面积且免租时，租金和有效日租才都为 0。
    effectiveDailyRent: area > 0
      ? finiteNumber(suppliedRate, rent / area)
      : null
  }
}

function normalizeEventImpact(value) {
  return {
    rent_delta: finiteNumber(value?.rent_delta ?? value?.rentDelta),
    area_delta: finiteNumber(value?.area_delta ?? value?.areaDelta),
    effective_daily_rent_impact: finiteNumber(
      value?.effective_daily_rent_impact ?? value?.rate_impact ?? value?.rateImpact
    )
  }
}

function hasEventImpact(value) {
  const impact = normalizeEventImpact(value)
  return (
    impact.rent_delta !== 0
    || impact.area_delta !== 0
    || impact.effective_daily_rent_impact !== 0
  )
}

export function eventImpactsForSegments(event, segmentKeys, segmentDefinitions = FALLBACK_RENT_SEGMENTS) {
  const labels = new Map(
    (Array.isArray(segmentDefinitions) ? segmentDefinitions : [])
      .map((item) => [String(item?.key || ''), String(item?.label || item?.key || '')])
  )
  return [...new Set(Array.isArray(segmentKeys) ? segmentKeys : [])]
    .map((segmentKey) => {
      const impact = segmentKey === 'overall'
        ? normalizeEventImpact(event)
        : normalizeEventImpact(event?.segments?.[segmentKey] || event?.segmentImpacts?.[segmentKey])
      return {
        segmentKey,
        label: labels.get(segmentKey) || segmentKey,
        rentDelta: impact.rent_delta,
        areaDelta: impact.area_delta,
        rateImpact: impact.effective_daily_rent_impact
      }
    })
    .filter((impact) => (
      impact.rentDelta !== 0
      || impact.areaDelta !== 0
      || impact.rateImpact !== 0
    ))
}

export function rentCoverageForRange(coverage, startDate, endDate) {
  const excluded = Array.isArray(coverage?.excluded) ? coverage.excluded : []
  const unlistedCount = Math.max(
    finiteNumber(coverage?.excludedContracts ?? coverage?.excluded_contracts) - excluded.length,
    0
  )
  const unknown = excluded.filter((item) => (
    !parseIsoDate(item?.startDate ?? item?.start_date)
    || !parseIsoDate(item?.endDate ?? item?.end_date)
    || (item?.startDate ?? item?.start_date) > (item?.endDate ?? item?.end_date)
  ))
  const affected = parseIsoDate(startDate) && parseIsoDate(endDate)
    ? excluded.filter((item) => {
        const itemStart = item?.startDate ?? item?.start_date
        const itemEnd = item?.endDate ?? item?.end_date
        return (
          parseIsoDate(itemStart)
          && parseIsoDate(itemEnd)
          && itemStart <= itemEnd
          && itemStart <= endDate
          && itemEnd >= startDate
        )
      })
    : []
  const unknownCount = unknown.length + unlistedCount
  return {
    isComplete: unknownCount === 0 && affected.length === 0,
    unknownCount,
    affectedCount: affected.length,
    affected
  }
}

export function normalizeRentAnalytics(payload = {}) {
  const segments = normalizedSegments(payload.segments)
  const segmentKeys = segments.map((item) => item.key)
  const sourceDaily = Array.isArray(payload.daily) ? payload.daily : []
  const sourceDates = sourceDaily
    .map((item) => String(item?.date || ''))
    .filter((value) => parseIsoDate(value))
    .sort()
  const today = parseIsoDate(payload.bounds?.today)
    ? payload.bounds.today
    : (sourceDates[sourceDates.length - 1] || '')
  const daily = sourceDaily
    .filter((item) => parseIsoDate(item?.date))
    .map((item) => {
      const date = String(item.date)
      const values = {}
      for (const key of segmentKeys) values[key] = normalizeSegmentValue(item.segments?.[key])
      return {
        date,
        timestamp: isoDateTimestamp(date),
        phase: item.phase === 'forecast' || (today && date > today) ? 'forecast' : 'history',
        segments: values
      }
    })
    .sort((a, b) => a.date.localeCompare(b.date))

  const firstDate = daily[0]?.date || ''
  const lastDate = daily[daily.length - 1]?.date || ''
  const minDate = parseIsoDate(payload.bounds?.min_date) ? payload.bounds.min_date : firstDate
  const maxDate = parseIsoDate(payload.bounds?.max_date) ? payload.bounds.max_date : lastDate
  const fallbackDefaultEnd = today && today <= maxDate ? today : maxDate
  const fallbackDefaultStart = addDays(addYearsClamped(fallbackDefaultEnd, -1), 1)
  const defaultRange = clampDateRange(
    payload.bounds?.default_start || fallbackDefaultStart,
    payload.bounds?.default_end || fallbackDefaultEnd,
    minDate,
    maxDate
  )
  const coverage = payload.coverage || {}
  const totalContracts = Math.max(finiteNumber(coverage.total_contracts), 0)
  const includedContracts = Math.max(finiteNumber(coverage.included_contracts), 0)
  const excludedContracts = Math.max(
    finiteNumber(coverage.excluded_contracts, Math.max(totalContracts - includedContracts, 0)),
    0
  )
  const coverageRate = finiteNumber(
    coverage.coverage_rate,
    totalContracts ? (includedContracts / totalContracts) * 100 : 100
  )
  const excluded = (Array.isArray(coverage.excluded) ? coverage.excluded : []).map((item) => {
    const rawStart = item?.start_date ?? item?.startDate
    const rawEnd = item?.end_date ?? item?.endDate
    const knownPeriod = (
      parseIsoDate(rawStart)
      && parseIsoDate(rawEnd)
      && rawStart <= rawEnd
    )
    return {
      ...item,
      startDate: knownPeriod ? String(rawStart) : '',
      endDate: knownPeriod ? String(rawEnd) : ''
    }
  })

  return {
    contractVersion: String(payload.contract_version || ''),
    segments,
    daily,
    events: (Array.isArray(payload.events) ? payload.events : [])
      .filter((item) => parseIsoDate(item?.date || item?.effect_date))
      .map((item) => {
        const segmentImpacts = item.segments && typeof item.segments === 'object'
          ? Object.fromEntries(
              Object.entries(item.segments)
                .filter(([key]) => Boolean(key))
                .map(([key, impact]) => [String(key), normalizeEventImpact(impact)])
            )
          : {}
        const impactedGroups = Object.entries(segmentImpacts)
          .filter(([, impact]) => hasEventImpact(impact))
          .map(([key]) => key)
        const explicitGroups = Array.isArray(item.groups)
          ? item.groups.map((group) => String(group))
          : [item.group || item.segment].filter(Boolean).map((group) => String(group))
        return {
          ...item,
          date: String(item.date || item.effect_date),
          group: String(item.group || item.segment || ''),
          groups: [...new Set([...explicitGroups, ...impactedGroups])],
          segments: segmentImpacts,
          segmentImpacts,
          contractId: item.contract_id ?? null,
          contractNo: String(item.contract_no || ''),
          tenantName: String(item.tenant_name || item.tenant || ''),
          label: String(item.label || item.description || '合同变化'),
          type: String(item.type || 'change'),
          rentDelta: finiteNumber(item.rent_delta),
          areaDelta: finiteNumber(item.area_delta),
          rateImpact: finiteNumber(item.effective_daily_rent_impact ?? item.rate_impact)
        }
      })
      .sort((a, b) => a.date.localeCompare(b.date)),
    bounds: {
      minDate,
      maxDate,
      today,
      defaultStart: defaultRange.start,
      defaultEnd: defaultRange.end
    },
    coverage: {
      totalContracts,
      includedContracts,
      excludedContracts,
      coverageRate,
      isComplete: (
        excludedContracts === 0
        && (totalContracts === 0 || includedContracts >= totalContracts)
        && coverageRate >= 100
      ),
      excluded
    }
  }
}

function lowerBound(dates, target) {
  let low = 0
  let high = dates.length
  while (low < high) {
    const middle = (low + high) >> 1
    if (dates[middle] < target) low = middle + 1
    else high = middle
  }
  return low
}

function upperBound(dates, target) {
  let low = 0
  let high = dates.length
  while (low < high) {
    const middle = (low + high) >> 1
    if (dates[middle] <= target) low = middle + 1
    else high = middle
  }
  return low
}

export function buildRentAnalyticsIndex(daily, segments) {
  const dates = daily.map((row) => row.date)
  const indexedSegments = {}
  for (const segment of segments) {
    const rentPrefix = new Float64Array(daily.length + 1)
    const areaPrefix = new Float64Array(daily.length + 1)
    for (let index = 0; index < daily.length; index += 1) {
      const value = daily[index].segments[segment.key] || {}
      rentPrefix[index + 1] = rentPrefix[index] + finiteNumber(value.rent)
      areaPrefix[index + 1] = areaPrefix[index] + Math.max(finiteNumber(value.area), 0)
    }
    indexedSegments[segment.key] = { rentPrefix, areaPrefix }
  }
  return { dates, segments: indexedSegments }
}

function emptyMetric(start, end) {
  return {
    start,
    end,
    days: inclusiveDayCount(start, end),
    rent: 0,
    areaDays: 0,
    averageArea: 0,
    effectiveDailyRent: null
  }
}

function metricFromIndex(index, segmentKey, startDate, endDate) {
  if (!index?.dates?.length || !index.segments?.[segmentKey]) return emptyMetric(startDate, endDate)
  const left = lowerBound(index.dates, startDate)
  const right = upperBound(index.dates, endDate)
  const segment = index.segments[segmentKey]
  const rent = segment.rentPrefix[right] - segment.rentPrefix[left]
  const areaDays = segment.areaPrefix[right] - segment.areaPrefix[left]
  const days = inclusiveDayCount(startDate, endDate)
  return {
    start: startDate,
    end: endDate,
    days,
    rent,
    areaDays,
    averageArea: days ? areaDays / days : 0,
    effectiveDailyRent: areaDays > 0 ? rent / areaDays : null
  }
}

export function summarizeRentRange(index, startDate, endDate, segmentKeys, today) {
  const result = {}
  for (const segmentKey of segmentKeys) {
    const total = metricFromIndex(index, segmentKey, startDate, endDate)
    const historyEnd = today && today < endDate ? today : endDate
    const forecastStart = today ? addDays(today, 1) : ''
    const history = startDate <= historyEnd
      ? metricFromIndex(index, segmentKey, startDate, historyEnd)
      : null
    const forecast = forecastStart && forecastStart <= endDate
      ? metricFromIndex(index, segmentKey, forecastStart < startDate ? startDate : forecastStart, endDate)
      : null
    result[segmentKey] = { total, history, forecast }
  }
  return result
}

function percentChange(current, previous) {
  if (current == null || previous == null || previous === 0) return null
  return ((current - previous) / Math.abs(previous)) * 100
}

function isRangeAvailable(range, bounds) {
  return Boolean(
    range
    && range.start >= bounds.minDate
    && range.end <= bounds.maxDate
  )
}

export function compareRentRange(index, startDate, endDate, segmentKey, bounds) {
  const current = metricFromIndex(index, segmentKey, startDate, endDate)
  const previousRange = previousEqualRange(startDate, endDate)
  const previous = isRangeAvailable(previousRange, bounds)
    ? metricFromIndex(index, segmentKey, previousRange.start, previousRange.end)
    : null
  const yoyRange = inclusiveDayCount(startDate, endDate) <= 366
    ? yearAgoRange(startDate, endDate)
    : null
  const yoy = isRangeAvailable(yoyRange, bounds)
    ? metricFromIndex(index, segmentKey, yoyRange.start, yoyRange.end)
    : null
  return {
    current,
    previous,
    yoy,
    previousChange: percentChange(current.effectiveDailyRent, previous?.effectiveDailyRent),
    yoyChange: percentChange(current.effectiveDailyRent, yoy?.effectiveDailyRent),
    previousRange,
    yoyRange
  }
}

function weekStart(value) {
  const date = parseIsoDate(value)
  if (!date) return ''
  const day = date.getUTCDay() || 7
  date.setUTCDate(date.getUTCDate() - day + 1)
  return formatIsoDate(date)
}

function bucketKey(date, granularity) {
  if (granularity === 'month') return date.slice(0, 7)
  if (granularity === 'week') return weekStart(date)
  return date
}

export function aggregateRentTimeline(daily, segmentKeys, granularity = 'day') {
  const buckets = new Map()
  for (const row of daily) {
    for (const segmentKey of segmentKeys) {
      const value = row.segments[segmentKey]
      if (!value) continue
      const key = `${segmentKey}|${row.phase}|${bucketKey(row.date, granularity)}`
      const bucket = buckets.get(key) || {
        segmentKey,
        phase: row.phase,
        startDate: row.date,
        endDate: row.date,
        rent: 0,
        areaDays: 0,
        days: 0
      }
      if (row.date < bucket.startDate) bucket.startDate = row.date
      if (row.date > bucket.endDate) bucket.endDate = row.date
      bucket.rent += finiteNumber(value.rent)
      bucket.areaDays += Math.max(finiteNumber(value.area), 0)
      bucket.days += 1
      buckets.set(key, bucket)
    }
  }
  return [...buckets.values()]
    .map((bucket) => ({
      ...bucket,
      timestamp: isoDateTimestamp(bucket.startDate),
      averageArea: bucket.days ? bucket.areaDays / bucket.days : 0,
      effectiveDailyRent: bucket.areaDays > 0 ? bucket.rent / bucket.areaDays : null
    }))
    .sort((a, b) => a.timestamp - b.timestamp || a.segmentKey.localeCompare(b.segmentKey))
}

export function rentRowsForRange(daily, startDate, endDate) {
  if (!Array.isArray(daily) || !parseIsoDate(startDate) || !parseIsoDate(endDate)) return []
  return daily.filter((row) => row.date >= startDate && row.date <= endDate)
}

export function alignedComparisonTimeline(daily, range, segmentKey, mode, granularity) {
  const sourceRange = mode === 'yoy'
    ? yearAgoRange(range.start, range.end)
    : previousEqualRange(range.start, range.end)
  if (!sourceRange) return []
  const offsetDays = mode === 'previous' ? inclusiveDayCount(range.start, range.end) : null
  const alignedRows = daily
    .filter((row) => row.date >= sourceRange.start && row.date <= sourceRange.end)
    .map((row) => {
      const alignedDate = mode === 'yoy'
        ? addYearsClamped(row.date, 1)
        : addDays(row.date, offsetDays)
      return {
        ...row,
        date: alignedDate,
        timestamp: isoDateTimestamp(alignedDate),
        phase: 'comparison'
      }
    })
  return aggregateRentTimeline(alignedRows, [segmentKey], granularity)
}

export function eventsForRange(events, startDate, endDate, segmentKeys) {
  const selected = new Set(segmentKeys)
  return events
    .filter((item) => {
      if (item.date < startDate || item.date > endDate) return false
      if (selected.has('overall')) return true
      if (item.groups?.length) return item.groups.some((group) => selected.has(group))
      const segmentImpacts = item.segments && typeof item.segments === 'object'
        ? item.segments
        : (item.segmentImpacts || {})
      return [...selected].some((key) => hasEventImpact(segmentImpacts[key]))
    })
    .sort((a, b) => {
      const impactA = normalizeEventImpact(a)
      const impactB = normalizeEventImpact(b)
      const magnitudeA = Math.abs(impactA.effective_daily_rent_impact)
        + Math.abs(impactA.rent_delta)
        + Math.abs(impactA.area_delta)
      const magnitudeB = Math.abs(impactB.effective_daily_rent_impact)
        + Math.abs(impactB.rent_delta)
        + Math.abs(impactB.area_delta)
      return magnitudeB - magnitudeA || a.date.localeCompare(b.date)
    })
}

export function rentExportQuery(startDate, endDate, segmentKeys) {
  const params = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
    segments: segmentKeys.join(',')
  })
  return params.toString()
}
