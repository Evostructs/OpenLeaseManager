import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  addYearsClamped,
  aggregateRentTimeline,
  buildRentAnalyticsIndex,
  chooseTimelineGranularity,
  compareRentRange,
  eventImpactsForSegments,
  eventsForRange,
  fitRentExportImage,
  hasFutureRentData,
  inclusiveDayCount,
  normalizeRentAnalytics,
  previousEqualRange,
  rentCoverageForRange,
  rentRowsForRange,
  summarizeRentRange
} from '../src/lib/rentAnalyticsDomain.js'

const payload = {
  contract_version: 'rent-analytics-v1',
  bounds: {
    min_date: '2024-02-27',
    max_date: '2025-03-02',
    today: '2024-03-01',
    default_start: '2024-02-28',
    default_end: '2024-03-01'
  },
  segments: [
    { key: 'overall', label: '整体' },
    { key: 'south_office', label: '南塔办公' },
    { key: 'north_office', label: '北塔办公' },
    { key: 'commercial', label: '商业配套' }
  ],
  coverage: {
    total_contracts: 4,
    included_contracts: 3,
    excluded_contracts: 1,
    coverage_rate: 75,
    excluded: [{ contract_no: 'BAD-1', reason: '缺少面积' }]
  },
  daily: [
    {
      date: '2024-02-27',
      phase: 'history',
      segments: {
        overall: { rent: 20, area: 10, effective_daily_rent: 2 },
        south_office: { rent: 20, area: 10, effective_daily_rent: 2 }
      }
    },
    {
      date: '2024-02-28',
      phase: 'history',
      segments: {
        overall: { rent: 20, area: 10, effective_daily_rent: 2 },
        south_office: { rent: 20, area: 10, effective_daily_rent: 2 }
      }
    },
    {
      date: '2024-02-29',
      phase: 'history',
      segments: {
        overall: { rent: 0, area: 10, effective_daily_rent: 0 },
        south_office: { rent: 0, area: 10, effective_daily_rent: 0 }
      }
    },
    {
      date: '2024-03-01',
      phase: 'history',
      segments: {
        overall: { rent: 60, area: 30, effective_daily_rent: 2 },
        south_office: { rent: 20, area: 10, effective_daily_rent: 2 },
        north_office: { rent: 40, area: 20, effective_daily_rent: 2 }
      }
    },
    {
      date: '2024-03-02',
      phase: 'forecast',
      segments: {
        overall: { rent: 0, area: 0, effective_daily_rent: 0 },
        south_office: { rent: 0, area: 0, effective_daily_rent: 0 }
      }
    },
    {
      date: '2025-02-28',
      phase: 'forecast',
      segments: {
        overall: { rent: 30, area: 10, effective_daily_rent: 3 }
      }
    },
    {
      date: '2025-03-01',
      phase: 'forecast',
      segments: {
        overall: { rent: 30, area: 10, effective_daily_rent: 3 }
      }
    },
    {
      date: '2025-03-02',
      phase: 'forecast',
      segments: {
        overall: { rent: 30, area: 10, effective_daily_rent: 3 }
      }
    }
  ]
}

test('normalization distinguishes a vacant day from a rent-free occupied day', () => {
  const normalized = normalizeRentAnalytics(payload)
  const freeDay = normalized.daily.find((item) => item.date === '2024-02-29')
  const vacantDay = normalized.daily.find((item) => item.date === '2024-03-02')

  assert.equal(freeDay.segments.overall.effectiveDailyRent, 0)
  assert.equal(vacantDay.segments.overall.effectiveDailyRent, null)
  assert.equal(normalized.coverage.coverageRate, 75)
  assert.equal(normalized.coverage.isComplete, false)
  assert.equal(normalizeRentAnalytics({
    ...payload,
    coverage: {
      total_contracts: 4,
      included_contracts: 4,
      excluded_contracts: 0,
      coverage_rate: 100
    }
  }).coverage.isComplete, true)
})

test('range summary uses total accrued rent divided by total area-days', () => {
  const normalized = normalizeRentAnalytics(payload)
  const index = buildRentAnalyticsIndex(normalized.daily, normalized.segments)
  const summary = summarizeRentRange(
    index,
    '2024-02-28',
    '2024-03-01',
    ['overall'],
    normalized.bounds.today
  ).overall.total

  assert.equal(summary.rent, 80)
  assert.equal(summary.areaDays, 50)
  assert.equal(summary.effectiveDailyRent, 1.6)
  assert.equal(summary.averageArea, 50 / 3)
})

test('summary separates history and signed forecast at the day after today', () => {
  const normalized = normalizeRentAnalytics(payload)
  const index = buildRentAnalyticsIndex(normalized.daily, normalized.segments)
  const summary = summarizeRentRange(index, '2024-03-01', '2024-03-02', ['overall'], '2024-03-01').overall

  assert.equal(summary.history.start, '2024-03-01')
  assert.equal(summary.history.end, '2024-03-01')
  assert.equal(summary.forecast.start, '2024-03-02')
  assert.equal(summary.forecast.end, '2024-03-02')
  assert.equal(summary.forecast.effectiveDailyRent, null)
})

test('daily aggregation preserves a null chart gap for zero-area dates', () => {
  const normalized = normalizeRentAnalytics(payload)
  const points = aggregateRentTimeline(normalized.daily, ['overall'], 'day')
  const vacantPoint = points.find((item) => item.startDate === '2024-03-02')

  assert.equal(vacantPoint.areaDays, 0)
  assert.equal(vacantPoint.effectiveDailyRent, null)
})

test('range-first aggregation keeps partial month edges exact', () => {
  const normalized = normalizeRentAnalytics(payload)
  const rows = rentRowsForRange(normalized.daily, '2024-02-28', '2024-03-01')
  const points = aggregateRentTimeline(rows, ['overall'], 'month')
  const february = points.find((item) => item.startDate === '2024-02-28')

  assert.equal(february.endDate, '2024-02-29')
  assert.equal(february.rent, 20)
  assert.equal(february.areaDays, 20)
  assert.equal(february.effectiveDailyRent, 1)
})

test('range helpers are inclusive and leap-year safe', () => {
  assert.equal(inclusiveDayCount('2024-02-28', '2024-03-01'), 3)
  assert.deepEqual(previousEqualRange('2024-02-28', '2024-03-01'), {
    start: '2024-02-25',
    end: '2024-02-27'
  })
  assert.equal(addYearsClamped('2024-02-29', -1), '2023-02-28')
})

test('display granularity adapts to the committed window', () => {
  assert.equal(chooseTimelineGranularity('2024-01-01', '2024-06-30'), 'day')
  assert.equal(chooseTimelineGranularity('2024-01-01', '2025-01-01'), 'week')
  assert.equal(chooseTimelineGranularity('2021-01-01', '2025-01-01'), 'month')
})

test('future preset availability requires meaningful signed-contract data after today', () => {
  const normalized = normalizeRentAnalytics(payload)
  assert.equal(hasFutureRentData(normalized.daily, normalized.bounds.today), true)
  assert.equal(
    hasFutureRentData(
      normalized.daily.filter((row) => row.date <= normalized.bounds.today),
      normalized.bounds.today
    ),
    false
  )
  assert.equal(
    hasFutureRentData([
      {
        date: '2024-03-02',
        segments: { overall: { rent: 0, area: 0, effectiveDailyRent: null } }
      }
    ], normalized.bounds.today),
    false
  )
})

test('PNG chart fitting preserves aspect ratio when height is the limiting edge', () => {
  const fitted = fitRentExportImage(1000, 2000, {
    minWidth: 1500,
    maxWidth: 2200,
    maxHeight: 1700
  })

  assert.deepEqual({ width: fitted.width, height: fitted.height }, { width: 850, height: 1700 })
  assert.equal(fitted.width / fitted.height, 0.5)
})

test('comparison exposes equal-window and year-over-year changes only when available', () => {
  const normalized = normalizeRentAnalytics(payload)
  const index = buildRentAnalyticsIndex(normalized.daily, normalized.segments)
  const comparison = compareRentRange(
    index,
    '2025-02-28',
    '2025-03-02',
    'overall',
    normalized.bounds
  )

  assert.equal(comparison.current.effectiveDailyRent, 3)
  assert.equal(comparison.yoy.effectiveDailyRent, 1.6)
  assert.ok(Math.abs(comparison.yoyChange - 87.5) < 0.0001)
})

test('contract events preserve segment impacts and filter by every selected segment', () => {
  const sourceEvents = [
    {
      date: '2024-03-01',
      label: '南塔调租',
      rent_delta: 100,
      segments: {
        south_office: {
          rent_delta: 100,
          area_delta: 0,
          effective_daily_rent_impact: 0.2
        },
        north_office: {
          rent_delta: 0,
          area_delta: 0,
          effective_daily_rent_impact: 0
        }
      }
    },
    {
      date: '2024-03-01',
      label: '北塔新增',
      rent_delta: 200,
      segments: {
        north_office: {
          rent_delta: 200,
          area_delta: 50,
          effective_daily_rent_impact: 0.1
        }
      }
    },
    {
      date: '2024-03-01',
      label: '仅整体事件',
      rent_delta: 50
    }
  ]
  const normalized = normalizeRentAnalytics({ ...payload, events: sourceEvents })

  assert.deepEqual(normalized.events[0].segments.south_office, {
    rent_delta: 100,
    area_delta: 0,
    effective_daily_rent_impact: 0.2
  })
  assert.deepEqual(
    eventsForRange(sourceEvents, '2024-03-01', '2024-03-01', ['south_office'])
      .map((item) => item.label),
    ['南塔调租']
  )
  assert.deepEqual(
    eventsForRange(sourceEvents, '2024-03-01', '2024-03-01', ['north_office'])
      .map((item) => item.label),
    ['北塔新增']
  )
  assert.equal(
    eventsForRange(sourceEvents, '2024-03-01', '2024-03-01', ['commercial']).length,
    0
  )
  assert.equal(
    eventsForRange(sourceEvents, '2024-03-01', '2024-03-01', ['overall']).length,
    3
  )

  const multiSegmentImpacts = eventImpactsForSegments({
    rentDelta: 300,
    areaDelta: 50,
    rateImpact: 0.3,
    segments: {
      south_office: {
        rent_delta: 100,
        area_delta: 0,
        effective_daily_rent_impact: 0.2
      },
      north_office: {
        rent_delta: 200,
        area_delta: 50,
        effective_daily_rent_impact: 0.1
      },
      commercial: {
        rent_delta: 0,
        area_delta: 0,
        effective_daily_rent_impact: 0
      }
    }
  }, ['overall', 'south_office', 'north_office', 'commercial'], normalized.segments)

  assert.deepEqual(
    multiSegmentImpacts.map((item) => item.segmentKey),
    ['overall', 'south_office', 'north_office']
  )
  assert.equal(multiSegmentImpacts[1].label, '南塔办公')
  assert.equal(multiSegmentImpacts[2].rentDelta, 200)
})

test('excluded contract periods only block comparison windows they overlap', () => {
  const knownCoverage = normalizeRentAnalytics({
    ...payload,
    coverage: {
      total_contracts: 2,
      included_contracts: 1,
      excluded_contracts: 1,
      coverage_rate: 50,
      excluded: [{
        contract_no: 'OLD-BAD',
        start_date: '2023-02-01',
        end_date: '2023-03-31',
        reasons: ['缺少面积']
      }]
    }
  }).coverage

  assert.equal(knownCoverage.excluded[0].startDate, '2023-02-01')
  assert.equal(rentCoverageForRange(knownCoverage, '2024-02-01', '2024-03-01').isComplete, true)
  assert.equal(rentCoverageForRange(knownCoverage, '2023-02-15', '2023-03-01').affectedCount, 1)

  const unknownCoverage = normalizeRentAnalytics(payload).coverage
  const unknownStatus = rentCoverageForRange(unknownCoverage, '2024-02-01', '2024-03-01')
  assert.equal(unknownStatus.isComplete, false)
  assert.equal(unknownStatus.unknownCount, 1)
})

test('timeline UI keeps all event markers and labels split historical and forecast details', () => {
  const source = readFileSync(
    new URL('../src/components/RentTimelineAnalytics.vue', import.meta.url),
    'utf8'
  )

  assert.doesNotMatch(source, /markers\.length\s*>=\s*60/)
  assert.match(source, /v-for="impact in item\.displayImpacts"/)
  assert.match(source, /metricRangeDescription\(card\.history\)/)
  assert.match(source, /metricRangeDescription\(card\.forecast\)/)
})
