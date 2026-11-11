const BUSINESS_TABS = Object.freeze([
  'dashboard',
  'operations',
  'analytics',
  'evidence',
  'ai-assist',
  'risks',
  'contracts',
  'customers',
  'rooms'
])

const PROFILE_CONTROLLED_TABS = Object.freeze([
  ...BUSINESS_TABS,
  'exchange',
  'receivables'
])

const OPERATIONS_ROUTE_TABS = new Set([
  ...PROFILE_CONTROLLED_TABS,
  'audit',
  'tools'
])

export const WORKSPACE_LEADERSHIP = 'leadership'
export const WORKSPACE_OPERATIONS = 'operations'

function rows(value) {
  return Array.isArray(value) ? value : []
}

function normalizedText(value) {
  return value === undefined || value === null ? '' : String(value).trim()
}

function normalizedPropertyScope(propertyScope = {}) {
  let type = normalizedText(propertyScope?.type || propertyScope?.scopeType).toLowerCase()
  let code = normalizedText(propertyScope?.code || propertyScope?.scopeCode)
  const buildingCode = normalizedText(propertyScope?.buildingCode)
  const portfolioCode = normalizedText(propertyScope?.portfolioCode)
  if (!type && buildingCode) type = 'building'
  if (!type && portfolioCode) type = 'portfolio'
  if (!code && type === 'building') code = buildingCode
  if (!code && type === 'portfolio') code = portfolioCode
  return ['building', 'portfolio'].includes(type) && code
    ? { type, code }
    : { type: '', code: '' }
}

function findByCode(items, code) {
  const normalizedCode = normalizedText(code).toLowerCase()
  if (!normalizedCode) return null
  return rows(items).find(
    (item) => normalizedText(item?.code).toLowerCase() === normalizedCode
  ) || null
}

function operationsProfile(item) {
  const profile = item?.operations_profile
  return profile && typeof profile === 'object' && !Array.isArray(profile) ? profile : null
}

function defaultPropertyScope(user = {}) {
  const summary = user?.scope || {}
  const buildings = rows(summary?.items)
  const defaultBuilding = findByCode(buildings, summary?.default?.code)
  if (defaultBuilding) return { type: 'building', code: normalizedText(defaultBuilding.code) }
  if (buildings[0]?.code) return { type: 'building', code: normalizedText(buildings[0].code) }

  const portfolios = rows(summary?.portfolio?.items)
  const defaultPortfolio = findByCode(portfolios, summary?.portfolio?.default?.code)
  if (defaultPortfolio) return { type: 'portfolio', code: normalizedText(defaultPortfolio.code) }
  if (portfolios[0]?.code) return { type: 'portfolio', code: normalizedText(portfolios[0].code) }
  return { type: '', code: '' }
}

function propertyScopeItems(user = {}, propertyScope = {}) {
  const summary = user?.scope || {}
  const buildings = rows(summary?.items)
  const portfolios = rows(summary?.portfolio?.items)
  const selected = normalizedPropertyScope(propertyScope)
  const resolved = selected.type ? selected : defaultPropertyScope(user)

  if (resolved.type === 'building') {
    return [findByCode(buildings, resolved.code)]
  }
  if (resolved.type === 'portfolio') {
    const portfolio = findByCode(portfolios, resolved.code)
    const buildingCodes = rows(portfolio?.building_codes)
    return buildingCodes.length
      ? buildingCodes.map((code) => findByCode(buildings, code))
      : [null]
  }
  return []
}

function profileAvailableTabs(profile) {
  return new Set(rows(profile?.available_tabs).map(normalizedText).filter(Boolean))
}

function operationsProfilePolicy(user = {}, propertyScope = {}) {
  const buildings = rows(user?.scope?.items)
  const enforced = buildings.some((item) => operationsProfile(item))
  if (!enforced) return { enforced: false, availableTabs: new Set(), defaultPage: '' }

  const items = propertyScopeItems(user, propertyScope)
  if (!items.length) return { enforced: true, availableTabs: new Set(), defaultPage: '' }
  const requestedScope = normalizedPropertyScope(propertyScope)
  const effectiveScope = requestedScope.type ? requestedScope : defaultPropertyScope(user)

  let availableTabs = null
  const defaultPages = []
  for (const item of items) {
    const profile = operationsProfile(item)
    if (!profile) return { enforced: true, availableTabs: new Set(), defaultPage: '' }
    const profileTabs = profileAvailableTabs(profile)
    availableTabs = availableTabs === null
      ? profileTabs
      : new Set([...availableTabs].filter((tab) => profileTabs.has(tab)))
    defaultPages.push(normalizedText(profile?.default_page))
  }

  const distinctDefaults = new Set(defaultPages)
  if (effectiveScope.type === 'portfolio') {
    // Rent analytics currently has a single-building financial definition.
    // Keep multi-building portfolios on the supported read-only ledgers until
    // an explicit aggregation contract exists.
    availableTabs?.delete('analytics')
  }
  return {
    enforced: true,
    availableTabs: availableTabs || new Set(),
    defaultPage: distinctDefaults.size === 1 ? defaultPages[0] : ''
  }
}

function effectiveCapabilities(user) {
  return rows(user?.scope?.effective_capabilities)
}

function hasLeadershipOverview(user = {}) {
  return rows(user?.scope?.portfolio_overview?.items).length > 0
}

function hasBusinessScope(user = {}) {
  return rows(user?.scope?.items).length > 0
    || rows(user?.scope?.portfolio?.items).length > 0
}

function operationsTabs(user = {}, propertyScope = {}) {
  const allowed = [...BUSINESS_TABS]
  if (user?.role !== 'viewer' && effectiveCapabilities(user).includes('data_import_submit')) {
    allowed.push('exchange')
  }
  if (user?.role !== 'viewer') allowed.push('receivables')

  const profilePolicy = operationsProfilePolicy(user, propertyScope)
  const profileAllowed = profilePolicy.enforced
    ? allowed.filter((tab) => profilePolicy.availableTabs.has(tab))
    : allowed
  if (effectiveCapabilities(user).includes('platform_admin')) {
    profileAllowed.push('audit', 'tools')
  }
  return profileAllowed
}

export function availableWorkspacesForUser(user = {}) {
  const available = []
  if (hasLeadershipOverview(user)) available.push(WORKSPACE_LEADERSHIP)
  if (hasBusinessScope(user) || !available.length) available.push(WORKSPACE_OPERATIONS)
  return available
}

export function defaultWorkspaceForUser(user = {}) {
  return availableWorkspacesForUser(user)[0] || WORKSPACE_OPERATIONS
}

export function isWorkspaceSwitcherVisible(user = {}) {
  return availableWorkspacesForUser(user).length > 1
}

export function allowedTabsForWorkspace(user = {}, workspace = '', propertyScope = {}) {
  if (!availableWorkspacesForUser(user).includes(workspace)) return []
  if (workspace === WORKSPACE_LEADERSHIP) return ['overview']
  if (workspace === WORKSPACE_OPERATIONS) return operationsTabs(user, propertyScope)
  return []
}

export function landingTabForWorkspace(user = {}, workspace = '', propertyScope = {}) {
  const resolvedWorkspace = availableWorkspacesForUser(user).includes(workspace)
    ? workspace
    : defaultWorkspaceForUser(user)
  if (resolvedWorkspace === WORKSPACE_LEADERSHIP) return 'overview'

  const allowed = allowedTabsForWorkspace(user, resolvedWorkspace, propertyScope)
  const profileDefault = operationsProfilePolicy(user, propertyScope).defaultPage
  if (profileDefault && allowed.includes(profileDefault)) return profileDefault
  if (allowed.includes('dashboard')) return 'dashboard'
  return allowed[0] || ''
}

export function resolveWorkspaceTab(user = {}, workspace = '', requestedTab = '', propertyScope = {}) {
  const allowed = allowedTabsForWorkspace(user, workspace, propertyScope)
  if (allowed.includes(requestedTab)) return requestedTab
  return landingTabForWorkspace(user, workspace, propertyScope)
}

export function resolveEntryWorkspace(user = {}, requestedTab = '', options = {}) {
  const available = availableWorkspacesForUser(user)
  let workspace = defaultWorkspaceForUser(user)
  const hasValidPreference = options.defaultEntry && available.includes(options.preferredWorkspace)
  if (hasValidPreference) {
    workspace = options.preferredWorkspace
  } else if (!options.defaultEntry) {
    if (
      requestedTab === 'overview'
      && available.includes(WORKSPACE_LEADERSHIP)
    ) {
      workspace = WORKSPACE_LEADERSHIP
    } else if (
      OPERATIONS_ROUTE_TABS.has(requestedTab)
      && available.includes(WORKSPACE_OPERATIONS)
    ) {
      workspace = WORKSPACE_OPERATIONS
    }
  }
  const entryTab = hasValidPreference
    ? options.preferredLastTabs?.[workspace]
    : (options.defaultEntry ? '' : requestedTab)
  return {
    workspace,
    tab: resolveWorkspaceTab(user, workspace, entryTab, options.propertyScope)
  }
}

export function isPortfolioOverviewOnly(user = {}) {
  const available = availableWorkspacesForUser(user)
  return available.length === 1 && available[0] === WORKSPACE_LEADERSHIP
}

export function allowedTabsForUser(user = {}, propertyScope = {}) {
  const allowed = []
  if (hasLeadershipOverview(user)) allowed.push('overview')
  if (availableWorkspacesForUser(user).includes(WORKSPACE_OPERATIONS)) {
    allowed.push(...operationsTabs(user, propertyScope))
  }
  return allowed
}

export function landingTabForUser(user = {}, propertyScope = {}) {
  return landingTabForWorkspace(user, defaultWorkspaceForUser(user), propertyScope)
}

export function resolveAllowedTab(user = {}, requestedTab = '', propertyScope = {}) {
  const allowed = allowedTabsForUser(user, propertyScope)
  return allowed.includes(requestedTab) ? requestedTab : landingTabForUser(user, propertyScope)
}

export function resolveEntryTab(user = {}, requestedTab = '', options = {}) {
  return resolveEntryWorkspace(user, requestedTab, options).tab
}

export function overviewRouteQuery(portfolioCode, buildingCode = '') {
  const portfolio = String(portfolioCode || '').trim()
  const building = String(buildingCode || '').trim()
  if (!portfolio) return {}
  return {
    overview_portfolio: portfolio,
    ...(building ? { overview_building: building } : {})
  }
}

export function portfolioOverviewRequestPath(portfolioCode, asOfDate = '') {
  const params = new URLSearchParams()
  params.set('portfolio', String(portfolioCode || '').trim())
  const date = String(asOfDate || '').trim()
  if (date) params.set('as_of_date', date)
  return `/api/operations/portfolio-overview?${params.toString()}`
}
