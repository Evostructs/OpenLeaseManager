import messages from '../../../app/i18n/en.json' with { type: 'json' }
import patternKeys from '../../../app/i18n/patterns.json' with { type: 'json' }

const han = /[\u3400-\u9fff]/
const normalize = (value) => value.replace(/\s+/g, ' ').trim()
const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const patterns = patternKeys
  .filter((key) => /\{\d+\}/.test(key))
  .sort((a, b) => b.replace(/\{\d+\}/g, '').length - a.replace(/\{\d+\}/g, '').length)
  .map((key) => {
    const slots = [...key.matchAll(/\{(\d+)\}/g)].map((match) => match[1])
    const parts = key.split(/\{\d+\}/)
    return { regex: new RegExp(`^${parts.map(escapeRegex).join('(.*?)')}$`, 's'), slots, text: messages[key], literalLength: parts.join('').length }
  })
const cache = new Map()

// Only call at presentation boundaries. Never translate requests, persisted values,
// identifiers, user-entered names, or text inside a form's model.
export function translate(value, language = 'en') {
  if (language !== 'en' || typeof value !== 'string' || !han.test(value)) return value
  const key = normalize(value)
  let translated = cache.get(key)
  if (translated === undefined) {
    translated = messages[key]
    if (translated === undefined) {
      for (const pattern of patterns) {
        const match = pattern.regex.exec(key)
        if (!match) continue
        if (pattern.literalLength < 3 && !match.slice(1).every((part) => /^[\d.,+%\s-]*$/.test(part))) continue
        const args = Object.fromEntries(pattern.slots.map((slot, index) => [slot, match[index + 1]]))
        // Interpolated arguments are deliberately left untouched.
        translated = pattern.text.replace(/\{(\d+)\}/g, (_, slot) => args[slot] ?? '')
        break
      }
    }
    if (translated === undefined) {
      const pieces = key.split(/(\s+·\s+|\s+\/\s+|；|：)/)
      if (pieces.length > 1) translated = pieces.map((piece, index) => index % 2 ? piece.replace('；', '; ').replace('：', ': ') : translate(piece, language)).join('')
    }
    if (translated === undefined) {
      const units = { 间: ' units', 天: ' days', 租: ' leased', 空: ' vacant', 待办: ' to do', 条: ' records', 份: ' leases', 点: ' pp', 个月: ' months' }
      translated = key.replace(/(-?[\d,.]+)\s*(间|天|租|空|待办|条|份|点|个月)/g, (_, number, unit) => number + units[unit])
        .replace(/(-?[\d,.]+)万/g, (_, number) => `${Number(number.replaceAll(',', '')) * 10}k`)
        .replace(/(-?[\d,.]+)亿/g, (_, number) => `${Number(number.replaceAll(',', '')) * 100}m`)
    }
    if (cache.size > 3000) cache.clear()
    cache.set(key, translated)
  }
  return (value.match(/^\s*/)?.[0] || '') + translated + (value.match(/\s*$/)?.[0] || '')
}

export function readLocale(storage) {
  try { return storage?.getItem('openleaseops.locale') === 'zh-CN' ? 'zh-CN' : 'en' } catch { return 'en' }
}

export function persistLocale(language, storage) {
  const normalized = language === 'zh-CN' ? 'zh-CN' : 'en'
  try { storage?.setItem('openleaseops.locale', normalized) } catch { /* Private browsing: keep in memory. */ }
  return normalized
}
