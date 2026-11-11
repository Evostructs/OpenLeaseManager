import { translate } from './translate.js'

function richLabel(value, language) {
  if (typeof value !== 'string') return value
  // Formatter HTML stays intact; interpolate/escape business values in the caller.
  return value.split(/(<[^>]*>)/g).map((part) => part.startsWith('<') ? part : translate(part, language)).join('')
}

export function localizeChartOption(option, language) {
  if (language !== 'en') return option
  function visit(value, key = '', inData = false) {
    if (typeof value === 'function') {
      return key === 'formatter' ? function (...args) { return richLabel(value.apply(this, args), language) } : value
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
      if (key === 'axisLabel' && !result.formatter) result.formatter = (label) => translate(label, language)
      return result
    }
    if (typeof value === 'string' && !inData && ['name', 'text', 'subtext', 'formatter'].includes(key)) return translate(value, language)
    return value
  }
  const localized = visit(option)
  for (const axisName of ['xAxis', 'yAxis']) {
    for (const axis of [].concat(localized[axisName] || [])) {
      axis.axisLabel ||= {}
      axis.axisLabel.formatter ||= (label) => translate(label, language)
    }
  }
  // Legend lookup must use the same names as the localized series.
  for (const legend of [].concat(localized.legend || [])) {
    if (legend.data) legend.data = legend.data.map((item) => typeof item === 'string' ? translate(item, language) : { ...item, name: translate(item.name, language) })
    legend.formatter ||= (label) => translate(label, language)
  }
  return localized
}

export function restoreChartEvent(payload, originalOption) {
  if (!payload || payload.seriesIndex == null) return payload
  const series = [].concat(originalOption?.series || [])[payload.seriesIndex]
  return series?.name ? { ...payload, seriesName: series.name } : payload
}
