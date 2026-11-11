import { ref } from 'vue'
import { areaLabel } from './units.js'
import { validCurrency } from './currency.js'
import { translate, readLocale, persistLocale } from './translate.js'

function storage() {
  try { return globalThis.localStorage } catch { return undefined }
}
export const locale = ref(readLocale(storage()))
export const bookCurrency = ref('CNY')
export const currencyUnit = ref('CNY')
export const areaUnit = ref(storage()?.getItem('openleasemanager.area') === 'ft2' ? 'ft2' : 'm2')
export const fxRate = ref(1)
export const fxDate = ref('')
export const fxAvailable = ref(true)
export function setCurrencyUnit(code) { if (validCurrency(code)) currencyUnit.value = code }
export const t = (value) => {
  const text = areaLabel(translate(value, locale.value, currencyUnit.value), areaUnit.value)
  return ['面积', '出租面积', '合计面积'].includes(value) ? `${text} (${areaUnit.value === 'ft2' ? 'ft²' : 'm²'})` : text
}

function updateDocument() {
  if (typeof document === 'undefined') return
  document.documentElement.lang = locale.value
  document.title = locale.value === 'en' ? 'Open Lease Manager · Lease Management' : 'Open Lease Manager · 租赁管理系统'
}

export function setLocale(language) {
  locale.value = persistLocale(language, storage())
  updateDocument()
}

export const i18n = {
  install(app) {
    app.config.globalProperties.$t = t
    updateDocument()
  }
}

let preferencesReady
export function ensureDisplayUnits(loadQuote) {
  if (!preferencesReady) preferencesReady = (async () => {
    const requested = storage()?.getItem('openleasemanager.viewCurrency') || ''
    const payload = await loadQuote(requested)
    const value = payload.presentation
    bookCurrency.value = value.base
    currencyUnit.value = value.quote
    fxRate.value = value.rate
    fxDate.value = value.date
    fxAvailable.value = value.available
  })().catch(error => { preferencesReady = undefined; throw error })
  return preferencesReady
}

export function applyDisplayUnits(currency, area, value) {
  if (!validCurrency(currency) || !['m2', 'ft2'].includes(area)) throw new Error('Invalid display units')
  if (!value.available || value.quote !== currency) throw new Error(t('汇率暂不可用，请稍后重试。'))
  storage()?.setItem('openleasemanager.viewCurrency', currency)
  storage()?.setItem('openleasemanager.area', area)
  window.location.reload()
}
