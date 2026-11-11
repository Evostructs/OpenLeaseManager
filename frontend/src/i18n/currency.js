import currencies from '../../../app/i18n/currencies.json' with { type: 'json' }

export { currencies }
const codes = new Set(currencies.map(item => item.code))
export const validCurrency = code => codes.has(code)

// Only presentation labels enter here. Amounts, form values and business text stay intact.
export function currencyLabel(value, code = 'CNY') {
  if (typeof value !== 'string' || code === 'CNY' || !validCurrency(code)) return value
  return value.replace(/\b(?:CNY|RMB|yuan|renminbi)\b/gi, code)
    .replace(/[¥￥]/g, `${code} `)
    .replaceAll('人民币', code).replaceAll('万元', `万 ${code}`).replaceAll('亿元', `亿 ${code}`)
    .replace(/(?<![\u3400-\u9fff])元(?![\u3400-\u9fff])/g, code)
}
