import { canonicalAreaQuery } from '../i18n/units.js'
import { locale, areaUnit, currencyUnit, bookCurrency } from '../i18n/index.js'
const DEFAULT_SCOPE_CONTEXT = Object.freeze({
  userId: '',
  authorizationVersion: '',
  gatePolicyVersion: '',
  scopeType: '',
  scopeCode: '',
  metricDefinition: ''
})

let currentContext = DEFAULT_SCOPE_CONTEXT
let contextRevision = 0
const contextListeners = new Set()

function firstDefined(source, keys) {
  for (const key of keys) {
    if (source?.[key] !== undefined && source[key] !== null) return source[key]
  }
  return ''
}

function normalizedText(value) {
  return value === undefined || value === null ? '' : String(value).trim()
}

function isSelectableBuilding(item) {
  const code = normalizedText(item?.code)
  return Boolean(code) && (!item?.is_provisional || code.toUpperCase() === 'DEMO_A')
}

function normalizeContext(source = {}) {
  let scopeType = normalizedText(firstDefined(source, ['scopeType', 'scope_type'])).toLowerCase()
  let scopeCode = normalizedText(firstDefined(source, ['scopeCode', 'scope_code']))

  const buildingCode = normalizedText(firstDefined(source, ['buildingCode', 'building_code', 'building']))
  const portfolioCode = normalizedText(firstDefined(source, ['portfolioCode', 'portfolio_code', 'portfolio']))
  if (!scopeType && buildingCode) scopeType = 'building'
  if (!scopeType && portfolioCode) scopeType = 'portfolio'
  if (!scopeCode && scopeType === 'building') scopeCode = buildingCode
  if (!scopeCode && scopeType === 'portfolio') scopeCode = portfolioCode

  if (!['building', 'portfolio'].includes(scopeType) || !scopeCode) {
    scopeType = ''
    scopeCode = ''
  }

  return Object.freeze({
    userId: normalizedText(firstDefined(source, ['userId', 'user_id'])),
    authorizationVersion: normalizedText(
      firstDefined(source, ['authorizationVersion', 'authorization_version'])
    ),
    gatePolicyVersion: normalizedText(
      firstDefined(source, ['gatePolicyVersion', 'gate_policy_version'])
    ),
    scopeType,
    scopeCode,
    metricDefinition: normalizedText(
      firstDefined(source, [
        'metricDefinition',
        'metric_definition',
        'metricDefinitionVersion',
        'metric_definition_version'
      ])
    )
  })
}

function contextKey(context) {
  return JSON.stringify([
    context.userId || 'anonymous',
    context.authorizationVersion || 'unversioned',
    context.gatePolicyVersion || 'ungated',
    context.scopeType || 'none',
    context.scopeCode || 'none',
    context.metricDefinition || 'default'
  ])
}

function notifyContextChange(reason) {
  contextRevision += 1
  const snapshot = getPropertyScopeContext()
  for (const listener of contextListeners) listener(snapshot, reason)
}

export function setPropertyScopeContext(source = {}) {
  const next = normalizeContext(source)
  if (contextKey(next) === contextKey(currentContext)) return getPropertyScopeContext()
  currentContext = next
  notifyContextChange('change')
  return getPropertyScopeContext()
}

export function resetPropertyScopeContext() {
  currentContext = DEFAULT_SCOPE_CONTEXT
  notifyContextChange('reset')
  return getPropertyScopeContext()
}

export function getPropertyScopeContext() {
  return { ...currentContext }
}

export function propertyScopeNamespace() {
  return `${contextRevision}:${contextKey(currentContext)}`
}

export function buildAuthorizedPropertyOptions(summary = {}) {
  const portfolios = Array.isArray(summary?.portfolio?.items) ? summary.portfolio.items : []
  const buildings = (Array.isArray(summary?.items) ? summary.items : []).filter(isSelectableBuilding)
  return [
    ...portfolios
      .filter((item) => normalizedText(item?.code))
      .map((item) => ({
        value: `portfolio:${normalizedText(item.code)}`,
        label: `板块 · ${normalizedText(item.name) || normalizedText(item.code)}`
      })),
    ...buildings
      .map((item) => ({
        value: `building:${normalizedText(item.code)}`,
        label: normalizedText(item.name) || normalizedText(item.code)
      }))
  ]
}

export function buildAuthorizedPortfolioOverviewOptions(summary = {}) {
  const portfolios = Array.isArray(summary?.portfolio_overview?.items)
    ? summary.portfolio_overview.items
    : []
  return portfolios
    .filter((item) => normalizedText(item?.code))
    .map((item) => ({
      value: `portfolio_overview:${normalizedText(item.code)}`,
      label: `板块概况 · ${normalizedText(item.name) || normalizedText(item.code)}`
    }))
}

export function resolveAuthorizedPortfolioOverviewSelection(summary = {}, request = {}) {
  const portfolios = Array.isArray(summary?.portfolio_overview?.items)
    ? summary.portfolio_overview.items
    : []
  const requested = normalizedText(firstDefined(request, [
    'overview_portfolio',
    'overviewPortfolio'
  ]))
  const findCode = (code) => portfolios.find(
    (item) => normalizedText(item?.code).toLowerCase() === code.toLowerCase()
  )

  const requestedMatch = requested ? findCode(requested) : null
  if (requestedMatch) {
    return { type: 'portfolio_overview', code: normalizedText(requestedMatch.code) }
  }

  const defaultCode = normalizedText(summary?.portfolio_overview?.default?.code)
  const defaultMatch = defaultCode ? findCode(defaultCode) : null
  const fallback = defaultMatch || portfolios[0]
  return fallback?.code
    ? { type: 'portfolio_overview', code: normalizedText(fallback.code) }
    : { type: '', code: '' }
}

export function resolveAuthorizedPropertySelection(summary = {}, request = {}) {
  const buildings = (Array.isArray(summary?.items) ? summary.items : []).filter(isSelectableBuilding)
  const portfolios = Array.isArray(summary?.portfolio?.items) ? summary.portfolio.items : []
  const requestedBuilding = normalizedText(firstDefined(request, ['building', 'buildingCode', 'building_code']))
  const requestedPortfolio = normalizedText(firstDefined(request, ['portfolio', 'portfolioCode', 'portfolio_code']))
  const findCode = (items, code) => items.find(
    (item) => normalizedText(item?.code).toLowerCase() === code.toLowerCase()
  )

  // A URL is only a request for a scope. Never keep an unknown code from a
  // previous user or an authorization version that no longer grants it.
  if (requestedBuilding && !requestedPortfolio) {
    const match = findCode(buildings, requestedBuilding)
    if (match) return { type: 'building', code: normalizedText(match.code) }
  }
  if (requestedPortfolio && !requestedBuilding) {
    const match = findCode(portfolios, requestedPortfolio)
    if (match) return { type: 'portfolio', code: normalizedText(match.code) }
  }

  const defaultBuilding = findCode(buildings, normalizedText(summary?.default?.code))
  if (defaultBuilding) return { type: 'building', code: normalizedText(defaultBuilding.code) }
  if (buildings[0]?.code) return { type: 'building', code: normalizedText(buildings[0].code) }

  const defaultPortfolio = findCode(portfolios, normalizedText(summary?.portfolio?.default?.code))
  if (defaultPortfolio) return { type: 'portfolio', code: normalizedText(defaultPortfolio.code) }
  if (portfolios[0]?.code) return { type: 'portfolio', code: normalizedText(portfolios[0].code) }
  return { type: '', code: '' }
}

export function onPropertyScopeContextChange(listener) {
  if (typeof listener !== 'function') return () => {}
  contextListeners.add(listener)
  return () => contextListeners.delete(listener)
}

export function isPropertyScopeExemptPath(pathname) {
  return pathname === '/api/auth'
    || pathname.startsWith('/api/auth/')
    || pathname === '/api/audit/platform'
    || pathname.startsWith('/api/audit/platform/')
    || pathname === '/api/buildings'
    || pathname.startsWith('/api/buildings/')
    || pathname === '/api/version'
    || pathname === '/api/system'
    || pathname.startsWith('/api/system/')
    || pathname === '/api/settings'
    || pathname.startsWith('/api/settings/')
    || pathname === '/api/backups'
    || pathname.startsWith('/api/backups/')
}

function browserOrigin() {
  if (typeof window !== 'undefined' && window.location?.origin) return window.location.origin
  return 'http://lease-manager.local'
}

function isAbsoluteUrl(value) {
  return /^[a-z][a-z\d+.-]*:/i.test(value) || value.startsWith('//')
}

function serializeParsedUrl(parsed, raw) {
  if (isAbsoluteUrl(raw)) return parsed.toString()
  return `${parsed.pathname}${parsed.search}${parsed.hash}`
}

export function buildScopedApiUrl(path, options = {}) {
  const raw = String(path ?? '')
  if (!raw) return raw

  let parsed
  try {
    parsed = new URL(raw, browserOrigin())
  } catch {
    return raw
  }

  if (!parsed.pathname.startsWith('/api/')) return raw
  if (isPropertyScopeExemptPath(parsed.pathname)) {
    const scopeKeys = ['building', 'portfolio', 'building_id', 'portfolio_id']
    const hadScope = scopeKeys.some((key) => parsed.searchParams.has(key))
    if (!hadScope) return raw
    for (const key of scopeKeys) parsed.searchParams.delete(key)
    return serializeParsedUrl(parsed, raw)
  }
  if (options.includeScope === false) return raw

  const context = options.context ? normalizeContext(options.context) : currentContext
  if (context.scopeType === 'building') {
    parsed.searchParams.delete('portfolio')
    parsed.searchParams.set('building', context.scopeCode)
  } else if (context.scopeType === 'portfolio') {
    parsed.searchParams.delete('building')
    parsed.searchParams.set('portfolio', context.scopeCode)
  } else {
    return raw
  }

  return serializeParsedUrl(parsed, raw)
}

export function buildScopedDownloadUrl(path, options = {}) {
  const scoped = buildScopedApiUrl(path, options)
  const url = new URL(scoped, 'http://localhost')
  url.searchParams.set('lang', locale.value)
  url.searchParams.set('area_unit', areaUnit.value)
  url.searchParams.set('view_currency', /template/.test(url.pathname) ? bookCurrency.value : currencyUnit.value)
  url.searchParams.set('unit_export', '1')
  return canonicalAreaQuery(url.pathname + url.search + url.hash, areaUnit.value)
}
