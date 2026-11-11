const TEXT_KEYS = {
  summary: ['summary', 'judgement', 'judgment', 'analysis', 'conclusion', 'text', 'result', 'answer', 'content'],
  suggestion: [
    'suggestion',
    'summary',
    'suggested_action',
    'suggested_next_action',
    'recommendation',
    'advice',
    'next_action',
    'text',
    'result',
    'answer',
    'content'
  ],
  explanation: [
    'explanation',
    'risk_explanation',
    'risk_analysis',
    'risk_reason',
    'reason',
    'impact',
    'recommendation',
    'summary',
    'text',
    'result',
    'answer',
    'content'
  ]
}

const HIGHLIGHT_KEYS = ['highlights', 'key_points', 'keyPoints', 'points']
const ACTION_KEYS = [
  'suggested_actions',
  'suggestedActions',
  'actions',
  'suggestions',
  'next_steps',
  'nextSteps',
  'follow_up_points',
  'followUpPoints',
  'follow_up_actions',
  'recommendations',
  'todo'
]
const TECHNICAL_COPY_PATTERN = /\b(prompt|token|debug|provider|model|json|api)\b|接口|模型|调试|只读辅助|不会自动/i

function cleanText(value) {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return ''
  return String(value).trim()
}

function stripJsonFence(value) {
  const text = cleanText(value)
  if (!text) return ''
  const fenced = text.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i)
  return (fenced ? fenced[1] : text).trim()
}

function removeTrailingCommas(value) {
  return String(value || '').replace(/,\s*([}\]])/g, '$1')
}

function extractJsonCandidate(value) {
  const source = stripJsonFence(value)
  if (!source) return ''
  const firstObject = source.indexOf('{')
  const firstArray = source.indexOf('[')
  const starts = [firstObject, firstArray].filter((index) => index >= 0)
  if (!starts.length) return source

  const start = Math.min(...starts)
  const stack = []
  let inString = false
  let escaped = false

  for (let index = start; index < source.length; index += 1) {
    const char = source[index]
    if (inString) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === '"') {
        inString = false
      }
      continue
    }

    if (char === '"') {
      inString = true
      continue
    }
    if (char === '{' || char === '[') {
      stack.push(char)
      continue
    }
    if (char === '}' || char === ']') {
      const expected = char === '}' ? '{' : '['
      if (stack.at(-1) !== expected) return source.slice(start, index + 1)
      stack.pop()
      if (!stack.length) return source.slice(start, index + 1)
    }
  }

  return source.slice(start)
}

function parseJsonText(value) {
  const text = stripJsonFence(value)
  if (!text || !/[{[]/.test(text)) return null
  const candidates = [
    text,
    removeTrailingCommas(text),
    extractJsonCandidate(text),
    removeTrailingCommas(extractJsonCandidate(text))
  ].filter(Boolean)
  for (const candidate of [...new Set(candidates)]) {
    try {
      return JSON.parse(candidate)
    } catch {
      // Try the next repair candidate.
    }
  }
  return null
}

function decodeJsonString(value) {
  const text = String(value || '')
  try {
    return JSON.parse(`"${text.replace(/"/g, '\\"')}"`)
  } catch {
    return text.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\r/g, '')
  }
}

function extractStringField(source, keys) {
  const text = stripJsonFence(source)
  if (!text) return ''
  for (const key of keys) {
    const pattern = new RegExp(`["']${key}["']\\s*:\\s*"((?:\\\\.|[^"\\\\])*)"`, 'is')
    const match = text.match(pattern)
    if (match?.[1]) return cleanText(decodeJsonString(match[1]))
  }
  return ''
}

function extractArrayField(source, keys) {
  const text = stripJsonFence(source)
  if (!text) return []
  for (const key of keys) {
    const pattern = new RegExp(`["']${key}["']\\s*:\\s*\\[([\\s\\S]*?)\\]`, 'i')
    const match = text.match(pattern)
    if (!match?.[1]) continue
    const items = []
    const itemPattern = /"((?:\\.|[^"\\])*)"/g
    let itemMatch = itemPattern.exec(match[1])
    while (itemMatch) {
      const value = cleanText(decodeJsonString(itemMatch[1]))
      if (value) items.push(value)
      itemMatch = itemPattern.exec(match[1])
    }
    if (items.length) return items
  }
  return []
}

function salvageStructuredText(value, field) {
  const text = stripJsonFence(value)
  if (!text || !/[{[]|"\w+"\s*:/.test(text)) return null
  const keys = TEXT_KEYS[field] || [field]
  const main =
    extractStringField(text, keys) ||
    extractStringField(text, TEXT_KEYS.summary) ||
    extractStringField(text, TEXT_KEYS.suggestion) ||
    extractStringField(text, TEXT_KEYS.explanation)
  const highlights = extractArrayField(text, HIGHLIGHT_KEYS)
  const actions = extractArrayField(text, ACTION_KEYS)
  if (!main && !highlights.length && !actions.length) return null
  return {
    [field]: main,
    highlights,
    suggested_actions: actions
  }
}

function normalizeList(value) {
  const parsed = parseJsonText(value)
  const source = parsed || value
  if (Array.isArray(source)) {
    return source
      .map((item) => (typeof item === 'object' ? readableObjectText(item) : cleanText(item)))
      .filter(Boolean)
  }
  const text = cleanText(source)
  if (!text) return []
  return text
    .split(/\r?\n|；|;/)
    .map((item) => item.replace(/^[-*•\d.\s]+/, '').trim())
    .filter(Boolean)
}

function readableObjectText(value) {
  const normalized = normalizeAiResult(value, 'summary')
  return aiMainText(normalized, 'summary') || aiMainText(normalized, 'suggestion') || aiMainText(normalized, 'explanation')
}

function businessFacingText(value) {
  const text = cleanText(value)
  if (!text) return ''
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !TECHNICAL_COPY_PATTERN.test(line))
  return lines.join('\n')
}

function firstValue(source, keys) {
  if (!source || typeof source !== 'object') return ''
  for (const key of keys) {
    const text = cleanText(source[key])
    if (text) return text
  }
  return ''
}

function firstList(source, keys) {
  if (!source || typeof source !== 'object') return []
  for (const key of keys) {
    const values = normalizeList(source[key])
    if (values.length) return values
  }
  return []
}

export function normalizeAiResult(result, field = 'summary') {
  const fallbackField = field || 'summary'
  const parsedResult = parseJsonText(result)
  if (parsedResult) return normalizeAiResult(parsedResult, fallbackField)

  if (!result || typeof result !== 'object') {
    const salvaged = salvageStructuredText(result, fallbackField)
    if (salvaged) return salvaged
    return { [fallbackField]: cleanText(result), highlights: [], suggested_actions: [] }
  }

  const keys = TEXT_KEYS[fallbackField] || [fallbackField]
  const directText = firstValue(result, keys)
  const nested = parseJsonText(result[fallbackField]) || parseJsonText(directText)

  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    const nestedText = firstValue(nested, keys) || firstValue(nested, TEXT_KEYS.summary) || firstValue(nested, TEXT_KEYS.suggestion)
    return {
      ...result,
      ...nested,
      [fallbackField]: nestedText,
      highlights: firstList(nested, HIGHLIGHT_KEYS).length ? firstList(nested, HIGHLIGHT_KEYS) : firstList(result, HIGHLIGHT_KEYS),
      suggested_actions: firstList(nested, ACTION_KEYS).length ? firstList(nested, ACTION_KEYS) : firstList(result, ACTION_KEYS)
    }
  }

  const salvaged = salvageStructuredText(result[fallbackField], fallbackField) || salvageStructuredText(directText, fallbackField)
  if (salvaged) {
    return {
      ...result,
      ...salvaged,
      highlights: salvaged.highlights.length ? salvaged.highlights : firstList(result, HIGHLIGHT_KEYS),
      suggested_actions: salvaged.suggested_actions.length ? salvaged.suggested_actions : firstList(result, ACTION_KEYS)
    }
  }

  return {
    ...result,
    [fallbackField]: directText,
    highlights: firstList(result, HIGHLIGHT_KEYS),
    suggested_actions: firstList(result, ACTION_KEYS)
  }
}

export function aiMainText(result, field) {
  const normalized = normalizeAiResult(result, field)
  const text = firstValue(normalized, TEXT_KEYS[field] || [field])
  const parsed = parseJsonText(text)
  if (parsed && typeof parsed === 'object') {
    return aiMainText(parsed, field)
  }
  return businessFacingText(text)
}

export function aiHighlights(result, field = 'summary') {
  return normalizeAiResult(result, field).highlights || []
}

export function aiSuggestedActions(result, field = 'summary') {
  return normalizeAiResult(result, field).suggested_actions || []
}

export function aiResultText(result, field, title = '') {
  const normalized = normalizeAiResult(result, field)
  const lines = []
  const heading = String(title || '').trim()
  const main = aiMainText(normalized, field)
  const highlights = aiHighlights(normalized, field)
  const actions = aiSuggestedActions(normalized, field)

  if (heading) lines.push(heading)
  if (main) lines.push(main)
  if (highlights.length) lines.push(`重点：\n${highlights.map((item) => `- ${item}`).join('\n')}`)
  if (actions.length) lines.push(`建议动作：\n${actions.map((item) => `- ${item}`).join('\n')}`)
  return lines.join('\n\n')
}

export function hasAiResultText(result, field) {
  return Boolean(aiResultText(result, field).trim())
}

export function followUpPrefillFromSuggestion(result) {
  const normalized = normalizeAiResult(result, 'suggestion')
  const content = aiMainText(normalized, 'suggestion')
  const actions = aiSuggestedActions(normalized, 'suggestion')
  return {
    content,
    next_action: actions.join('\n')
  }
}

export async function writeTextToClipboard(text) {
  const value = String(text || '').trim()
  if (!value) return false
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      // Fall through to the selection-based copy path for browsers that expose
      // Clipboard API but reject it in the current focus or permission state.
    }
  }

  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    return document.execCommand('copy')
  } finally {
    document.body.removeChild(textarea)
  }
}
