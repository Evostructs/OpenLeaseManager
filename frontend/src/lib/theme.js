export const THEME_STORAGE_KEY = 'lease-manager-theme'
export const THEME_CHANGE_EVENT = 'lease-theme-change'

export function currentTheme() {
  if (typeof document === 'undefined') return 'light'
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

// 视图层 chart option 中硬编码的浅色系颜色在暗色模式下的重映射表。
// 语义色（绿/红/黄系列色）在两套主题下通用，不在表中。
const DARK_CHART_COLOR_MAP = new Map([
  ['#111827', '#e2e8f0'],
  ['#1f2937', '#e2e8f0'],
  ['#374151', '#cbd5e1'],
  ['#475569', '#b6c2d2'],
  ['#64748b', '#94a3b8'],
  ['#6b7280', '#9ca3af'],
  ['#94a3b8', '#7d8fa8'],
  ['#9ca3af', '#8b98ab'],
  ['#cbd5e1', '#5b6b82'],
  ['#d1d5db', 'rgba(148, 163, 184, 0.4)'],
  ['#e5e7eb', 'rgba(148, 163, 184, 0.24)'],
  ['#e2e8f0', 'rgba(148, 163, 184, 0.22)'],
  ['#eef2f7', 'rgba(148, 163, 184, 0.16)'],
  ['#f1f5f9', 'rgba(148, 163, 184, 0.12)'],
  ['#f8fafc', 'rgba(148, 163, 184, 0.08)'],
  ['#ffffff', 'rgba(15, 23, 42, 0.92)'],
  ['#fff', 'rgba(15, 23, 42, 0.92)'],
  ['#dbeafe', 'rgba(59, 130, 246, 0.22)'],
  ['#eff6ff', 'rgba(59, 130, 246, 0.14)'],
  ['#d1fae5', 'rgba(16, 185, 129, 0.18)'],
  ['#fef3c7', 'rgba(245, 158, 11, 0.18)'],
  ['#fee2e2', 'rgba(239, 68, 68, 0.18)']
])

// 深拷贝 chart option 并替换命中的颜色字符串，不修改原对象。
export function remapChartOptionForDark(value) {
  if (Array.isArray(value)) {
    return value.map(remapChartOptionForDark)
  }
  if (value && typeof value === 'object') {
    const output = {}
    for (const [key, item] of Object.entries(value)) {
      output[key] = remapChartOptionForDark(item)
    }
    return output
  }
  if (typeof value === 'string') {
    return DARK_CHART_COLOR_MAP.get(value.toLowerCase()) || value
  }
  return value
}
