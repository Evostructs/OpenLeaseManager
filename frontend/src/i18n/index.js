import { ref } from 'vue'
import { validCurrency } from './currency.js'
import { translate, readLocale, persistLocale } from './translate.js'

function storage() {
  try { return globalThis.localStorage } catch { return undefined }
}
export const locale = ref(readLocale(storage()))
export const currencyUnit = ref('CNY')
export function setCurrencyUnit(code) { if (validCurrency(code)) currencyUnit.value = code }
export const t = (value) => translate(value, locale.value, currencyUnit.value)

function updateDocument() {
  if (typeof document === 'undefined') return
  document.documentElement.lang = locale.value
  document.title = locale.value === 'en' ? 'OpenLeaseOps · Lease Management' : 'OpenLeaseOps · 租赁管理系统'
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
