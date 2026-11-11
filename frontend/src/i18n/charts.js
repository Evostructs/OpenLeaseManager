import { areaLabel } from './units.js'
import { translate } from './translate.js'

function richLabel(value, language, currency, area) {
  if (typeof value !== 'string') return value
  // Formatter HTML stays intact; interpolate/escape business values in the caller.
  return value.split(/(<[^>]*>)/g).map((part) => part.startsWith('<') ? part : areaLabel(translate(part, language, currency), area)).join('')
}

export function localizeChartOption(option, language, currency = 'CNY', area = 'm2') {
  if (language !== 'en' && currency === 'CNY' && area === 'm2') return option
  function visit(value, key = '', inData = false) {
    if (typeof value === 'function') {
      return ['formatter', 'valueFormatter'].includes(key) ? function (...args) { return richLabel(value.apply(this, args), language, currency, area) } : value
    }
    if (Array.isArray(value)) return value.map((item) => visit(item, key, inData || key === 'data'))
    if (value && typeof value === 'object') {
      const result = {}
      for (const [child, descriptor] of Object.entries(Object.getOwnPropertyDescriptors(value))) {
        if (['data', 'dataset', 'source', 'dimensions'].includes(child) || !('value' in descriptor)) {
          Object.defineProperty(result, child, descriptor)
        } else {
          result[child] = visit(descriptor.value, child, inData)
        }
      }
      if (key === 'axisLabel' && !result.formatter) result.formatter = (label) => areaLabel(translate(label, language, currency), area)
      return result
    }
    if (typeof value === 'string' && !inData && ['name', 'text', 'subtext', 'formatter'].includes(key)) return areaLabel(translate(value, language, currency), area)
    return value
  }
  const localized = visit(option)
  for (const axisName of ['xAxis', 'yAxis']) {
    for (const axis of [].concat(localized[axisName] || [])) {
      axis.axisLabel ||= {}
      axis.axisLabel.formatter ||= (label) => areaLabel(translate(label, language, currency), area)
    }
  }
  // Legend lookup must use the same names as the localized series.
  for (const legend of [].concat(localized.legend || [])) {
    if (legend.data) legend.data = legend.data.map((item) => typeof item === 'string' ? areaLabel(translate(item, language, currency), area) : { ...item, name: areaLabel(translate(item.name, language, currency), area) })
    legend.formatter ||= (label) => areaLabel(translate(label, language, currency), area)
  }
  return localized
}

export function restoreChartEvent(payload, originalOption) {
  if (!payload || payload.seriesIndex == null) return payload
  const series = [].concat(originalOption?.series || [])[payload.seriesIndex]
  return series?.name ? { ...payload, seriesName: series.name } : payload
}
