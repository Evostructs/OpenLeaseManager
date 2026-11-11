import assert from 'node:assert/strict'
import test from 'node:test'

import {
  WORKSPACE_LEADERSHIP,
  WORKSPACE_OPERATIONS,
  allowedTabsForUser,
  allowedTabsForWorkspace,
  availableWorkspacesForUser,
  defaultWorkspaceForUser,
  isPortfolioOverviewOnly,
  isWorkspaceSwitcherVisible,
  landingTabForUser,
  landingTabForWorkspace,
  overviewRouteQuery,
  portfolioOverviewRequestPath,
  resolveAllowedTab,
  resolveEntryTab,
  resolveEntryWorkspace,
  resolveWorkspaceTab
} from '../src/lib/navigationPolicy.js'

const overviewOnlyLeader = {
  role: 'viewer',
  scope: {
    items: [],
    portfolio: { items: [] },
    portfolio_overview: {
      items: [
        {
          code: 'DEMO_OFFICE_PORTFOLIO',
          name: '示例资产组合'
        }
      ]
    },
    effective_capabilities: []
  }
}

const singleBuildingOperator = {
  role: 'editor',
  scope: {
    items: [{ code: 'DEMO_A', name: '示例双塔中心', access_level: 'edit' }],
    portfolio: { items: [] },
    portfolio_overview: { items: [] },
    effective_capabilities: []
  }
}

const mixedWorkspaceAdmin = {
  role: 'admin',
  scope: {
    items: [{ code: 'DEMO_A', name: '示例双塔中心', access_level: 'edit' }],
    portfolio: { items: [] },
    portfolio_overview: {
      items: [{ code: 'DEMO_OFFICE_PORTFOLIO', name: '示例资产组合' }]
    },
    effective_capabilities: ['platform_admin']
  }
}

const profiledPortfolioAdmin = {
  role: 'admin',
  scope: {
    default: { code: 'DEMO_A' },
    items: [
      {
        code: 'DEMO_A',
        operations_profile: {
          default_page: 'contracts',
          data_adapter: 'live_detail',
          available_tabs: [
            'dashboard',
            'operations',
            'analytics',
            'evidence',
            'ai-assist',
            'risks',
            'contracts',
            'customers',
            'rooms',
            'receivables'
          ]
        }
      },
      {
        code: 'DEMO_B',
        operations_profile: {
          default_page: 'evidence',
          data_adapter: 'published_snapshot',
          available_tabs: ['dashboard', 'evidence', 'exchange']
        }
      }
    ],
    portfolio: {
      items: [
        {
          code: 'DEMO_OFFICE_PORTFOLIO',
          building_codes: ['DEMO_A', 'DEMO_B']
        }
      ]
    },
    portfolio_overview: {
      items: [{ code: 'DEMO_OFFICE_PORTFOLIO' }]
    },
    effective_capabilities: ['data_import_submit', 'platform_admin']
  }
}

test('pure leadership identities receive one read-only leadership workspace', () => {
  assert.deepEqual(availableWorkspacesForUser(overviewOnlyLeader), [WORKSPACE_LEADERSHIP])
  assert.equal(defaultWorkspaceForUser(overviewOnlyLeader), WORKSPACE_LEADERSHIP)
  assert.equal(isWorkspaceSwitcherVisible(overviewOnlyLeader), false)
  assert.deepEqual(allowedTabsForWorkspace(overviewOnlyLeader, WORKSPACE_LEADERSHIP), ['overview'])
  assert.deepEqual(allowedTabsForWorkspace(overviewOnlyLeader, WORKSPACE_OPERATIONS), [])
  assert.equal(landingTabForWorkspace(overviewOnlyLeader, WORKSPACE_LEADERSHIP), 'overview')
  assert.deepEqual(
    resolveEntryWorkspace(overviewOnlyLeader, 'contracts'),
    { workspace: WORKSPACE_LEADERSHIP, tab: 'overview' }
  )
})

test('single-building operators enter the operations workspace without a leadership shell', () => {
  assert.deepEqual(availableWorkspacesForUser(singleBuildingOperator), [WORKSPACE_OPERATIONS])
  assert.equal(defaultWorkspaceForUser(singleBuildingOperator), WORKSPACE_OPERATIONS)
  assert.equal(isWorkspaceSwitcherVisible(singleBuildingOperator), false)
  assert.equal(allowedTabsForWorkspace(singleBuildingOperator, WORKSPACE_OPERATIONS).includes('overview'), false)
  assert.equal(resolveWorkspaceTab(singleBuildingOperator, WORKSPACE_OPERATIONS, 'overview'), 'dashboard')
  assert.deepEqual(
    resolveEntryWorkspace(singleBuildingOperator, 'overview'),
    { workspace: WORKSPACE_OPERATIONS, tab: 'dashboard' }
  )
})

test('a stale preference cannot move a single-workspace identity away from its authorized entry', () => {
  assert.deepEqual(
    resolveEntryWorkspace(singleBuildingOperator, 'dashboard', {
      defaultEntry: true,
      preferredWorkspace: WORKSPACE_LEADERSHIP,
      preferredLastTabs: {
        [WORKSPACE_OPERATIONS]: 'contracts'
      }
    }),
    { workspace: WORKSPACE_OPERATIONS, tab: 'dashboard' }
  )
})

test('mixed identities use an explicit workspace boundary and keep valid deep links', () => {
  assert.deepEqual(
    availableWorkspacesForUser(mixedWorkspaceAdmin),
    [WORKSPACE_LEADERSHIP, WORKSPACE_OPERATIONS]
  )
  assert.equal(defaultWorkspaceForUser(mixedWorkspaceAdmin), WORKSPACE_LEADERSHIP)
  assert.equal(isWorkspaceSwitcherVisible(mixedWorkspaceAdmin), true)
  assert.deepEqual(
    resolveEntryWorkspace(mixedWorkspaceAdmin, 'dashboard', { defaultEntry: true }),
    { workspace: WORKSPACE_LEADERSHIP, tab: 'overview' }
  )
  assert.deepEqual(
    resolveEntryWorkspace(mixedWorkspaceAdmin, 'contracts'),
    { workspace: WORKSPACE_OPERATIONS, tab: 'contracts' }
  )
  assert.equal(resolveWorkspaceTab(mixedWorkspaceAdmin, WORKSPACE_OPERATIONS, 'customers'), 'customers')
  assert.equal(resolveWorkspaceTab(mixedWorkspaceAdmin, WORKSPACE_LEADERSHIP, 'contracts'), 'overview')
})

test('mixed identities restore a preferred default workspace without overriding deep links', () => {
  const preference = {
    preferredWorkspace: WORKSPACE_OPERATIONS,
    preferredLastTabs: {
      [WORKSPACE_LEADERSHIP]: 'overview',
      [WORKSPACE_OPERATIONS]: 'contracts'
    }
  }

  assert.deepEqual(
    resolveEntryWorkspace(mixedWorkspaceAdmin, 'dashboard', {
      defaultEntry: true,
      ...preference
    }),
    { workspace: WORKSPACE_OPERATIONS, tab: 'contracts' }
  )
  assert.deepEqual(
    resolveEntryWorkspace(mixedWorkspaceAdmin, 'overview', preference),
    { workspace: WORKSPACE_LEADERSHIP, tab: 'overview' }
  )
  assert.deepEqual(
    resolveEntryWorkspace(mixedWorkspaceAdmin, 'customers', preference),
    { workspace: WORKSPACE_OPERATIONS, tab: 'customers' }
  )
})

test('portfolio overview identity can only enter the leadership overview', () => {
  assert.equal(isPortfolioOverviewOnly(overviewOnlyLeader), true)
  assert.deepEqual(allowedTabsForUser(overviewOnlyLeader), ['overview'])
  assert.equal(landingTabForUser(overviewOnlyLeader), 'overview')
  assert.equal(resolveAllowedTab(overviewOnlyLeader, 'overview'), 'overview')
  assert.equal(resolveAllowedTab(overviewOnlyLeader, 'contracts'), 'overview')
  assert.equal(resolveAllowedTab(overviewOnlyLeader, 'evidence'), 'overview')
  assert.equal(resolveAllowedTab(overviewOnlyLeader, 'tools'), 'overview')
})

test('ordinary building users keep the existing role and platform navigation', () => {
  const viewer = {
    role: 'viewer',
    scope: {
      items: [{ code: 'DEMO_A' }],
      portfolio: { items: [] },
      portfolio_overview: { items: [] },
      effective_capabilities: []
    }
  }
  const platformAdmin = {
    role: 'admin',
    scope: {
      items: [{ code: 'DEMO_A' }],
      portfolio: { items: [{ code: 'DEMO_OFFICE_PORTFOLIO' }] },
      portfolio_overview: { items: [{ code: 'DEMO_OFFICE_PORTFOLIO' }] },
      effective_capabilities: ['platform_admin']
    }
  }

  assert.equal(isPortfolioOverviewOnly(viewer), false)
  assert.equal(landingTabForUser(viewer), 'dashboard')
  assert.equal(allowedTabsForUser(viewer).includes('receivables'), false)
  assert.equal(allowedTabsForUser(viewer).includes('evidence'), true)
  assert.equal(resolveAllowedTab(viewer, 'tools'), 'dashboard')

  const adminTabs = allowedTabsForUser(platformAdmin)
  assert.equal(adminTabs.includes('overview'), true)
  assert.equal(adminTabs.includes('receivables'), true)
  assert.equal(adminTabs.includes('audit'), true)
  assert.equal(adminTabs.includes('tools'), true)
  assert.equal(resolveAllowedTab(platformAdmin, 'contracts'), 'contracts')
  assert.equal(landingTabForUser(platformAdmin), 'overview')
  assert.equal(resolveEntryTab(platformAdmin, 'dashboard', { defaultEntry: true }), 'overview')
  assert.equal(resolveEntryTab(platformAdmin, 'dashboard'), 'dashboard')
  assert.equal(resolveEntryTab(platformAdmin, 'contracts'), 'contracts')
})

test('leadership overview URL and API parameters stay separate from business scope parameters', () => {
  assert.deepEqual(
    overviewRouteQuery('DEMO_OFFICE_PORTFOLIO', 'DEMO_A'),
    {
      overview_portfolio: 'DEMO_OFFICE_PORTFOLIO',
      overview_building: 'DEMO_A'
    }
  )
  assert.deepEqual(
    overviewRouteQuery('DEMO_OFFICE_PORTFOLIO'),
    { overview_portfolio: 'DEMO_OFFICE_PORTFOLIO' }
  )
  assert.equal(
    portfolioOverviewRequestPath('SCI TECH/FIN', '2026-08-05'),
    '/api/operations/portfolio-overview?portfolio=SCI+TECH%2FFIN&as_of_date=2026-08-05'
  )
})

test('only data submitters receive the file exchange workbench navigation', () => {
  const uploader = {
    role: 'editor',
    scope: {
      items: [{ code: 'DEMO_B', access_level: 'edit' }],
      portfolio: { items: [] },
      portfolio_overview: { items: [] },
      effective_capabilities: ['data_import_submit']
    }
  }
  const receiver = {
    role: 'viewer',
    scope: {
      items: [],
      portfolio: { items: [] },
      portfolio_overview: { items: [] },
      effective_capabilities: ['snapshot_exchange_receive']
    }
  }
  const viewerWithSubmitCapability = {
    role: 'viewer',
    scope: {
      items: [{ code: 'DEMO_B', access_level: 'edit' }],
      portfolio: { items: [] },
      portfolio_overview: { items: [] },
      effective_capabilities: ['data_import_submit']
    }
  }

  assert.equal(allowedTabsForUser(uploader).includes('exchange'), true)
  assert.equal(resolveAllowedTab(uploader, 'exchange'), 'exchange')
  assert.equal(allowedTabsForUser(receiver).includes('exchange'), false)
  assert.equal(resolveAllowedTab(receiver, 'exchange'), 'dashboard')
  assert.equal(allowedTabsForUser(viewerWithSubmitCapability).includes('exchange'), false)
})

test('a snapshot building exposes only its profile intersection and keeps platform tabs separate', () => {
  const snapshotScope = { type: 'building', code: 'DEMO_B' }
  assert.deepEqual(
    allowedTabsForWorkspace(profiledPortfolioAdmin, WORKSPACE_OPERATIONS, snapshotScope),
    ['dashboard', 'evidence', 'exchange', 'audit', 'tools']
  )
  assert.equal(
    resolveWorkspaceTab(profiledPortfolioAdmin, WORKSPACE_OPERATIONS, 'contracts', snapshotScope),
    'evidence'
  )
  assert.equal(
    resolveWorkspaceTab(profiledPortfolioAdmin, WORKSPACE_OPERATIONS, 'exchange', snapshotScope),
    'exchange'
  )
})

test('role and explicit capability still cap profile tabs', () => {
  const snapshotViewer = {
    ...profiledPortfolioAdmin,
    role: 'viewer',
    scope: {
      ...profiledPortfolioAdmin.scope,
      portfolio_overview: { items: [] },
      effective_capabilities: ['data_import_submit']
    }
  }
  const snapshotScope = { type: 'building', code: 'DEMO_B' }

  assert.deepEqual(
    allowedTabsForWorkspace(snapshotViewer, WORKSPACE_OPERATIONS, snapshotScope),
    ['dashboard', 'evidence']
  )
})

test('a portfolio uses the safe intersection of every member building profile', () => {
  const portfolioScope = { type: 'portfolio', code: 'DEMO_OFFICE_PORTFOLIO' }
  assert.deepEqual(
    allowedTabsForWorkspace(profiledPortfolioAdmin, WORKSPACE_OPERATIONS, portfolioScope),
    ['dashboard', 'evidence', 'audit', 'tools']
  )
  assert.equal(
    resolveWorkspaceTab(profiledPortfolioAdmin, WORKSPACE_OPERATIONS, 'contracts', portfolioScope),
    'dashboard'
  )
  assert.equal(
    resolveWorkspaceTab(profiledPortfolioAdmin, WORKSPACE_OPERATIONS, 'exchange', portfolioScope),
    'dashboard'
  )
})

test('a detail-only portfolio keeps supported ledgers but hides single-building rent analytics', () => {
  const detailProfile = {
    default_page: 'dashboard',
    data_adapter: 'live_detail',
    available_tabs: ['dashboard', 'analytics', 'contracts', 'customers', 'rooms', 'receivables']
  }
  const user = {
    ...profiledPortfolioAdmin,
    scope: {
      ...profiledPortfolioAdmin.scope,
      items: [
        { code: 'DETAIL_A', operations_profile: detailProfile },
        { code: 'DETAIL_B', operations_profile: detailProfile }
      ],
      portfolio: {
        items: [{ code: 'DETAIL_PORTFOLIO', building_codes: ['DETAIL_A', 'DETAIL_B'] }]
      }
    }
  }
  const scope = { type: 'portfolio', code: 'DETAIL_PORTFOLIO' }
  const allowed = allowedTabsForWorkspace(user, WORKSPACE_OPERATIONS, scope)

  assert.equal(allowed.includes('analytics'), false)
  assert.equal(allowed.includes('contracts'), true)
  assert.equal(allowed.includes('rooms'), true)
})

test('an invalid operations deep link falls back to the selected profile default page', () => {
  const snapshotScope = { type: 'building', code: 'DEMO_B' }
  assert.deepEqual(
    resolveEntryWorkspace(profiledPortfolioAdmin, 'contracts', { propertyScope: snapshotScope }),
    { workspace: WORKSPACE_OPERATIONS, tab: 'evidence' }
  )
  assert.deepEqual(
    resolveEntryWorkspace(profiledPortfolioAdmin, 'dashboard', {
      defaultEntry: true,
      preferredWorkspace: WORKSPACE_OPERATIONS,
      preferredLastTabs: { [WORKSPACE_OPERATIONS]: 'customers' },
      propertyScope: snapshotScope
    }),
    { workspace: WORKSPACE_OPERATIONS, tab: 'evidence' }
  )
})

test('missing profiles retain the Phase 1 role and platform behavior', () => {
  assert.equal(
    allowedTabsForWorkspace(mixedWorkspaceAdmin, WORKSPACE_OPERATIONS).includes('contracts'),
    true
  )
  assert.equal(
    allowedTabsForWorkspace(mixedWorkspaceAdmin, WORKSPACE_OPERATIONS).includes('tools'),
    true
  )
})

test('the central profile is the only product capability filter', () => {
  const centrallyExtended = structuredClone(profiledPortfolioAdmin)
  centrallyExtended.scope.items[1].operations_profile.available_tabs.push('analytics')
  const snapshotScope = { type: 'building', code: 'DEMO_B' }

  assert.equal(
    allowedTabsForWorkspace(
      centrallyExtended,
      WORKSPACE_OPERATIONS,
      snapshotScope
    ).includes('analytics'),
    true
  )
  assert.equal(
    resolveWorkspaceTab(
      centrallyExtended,
      WORKSPACE_OPERATIONS,
      'analytics',
      snapshotScope
    ),
    'analytics'
  )
})

test('an unavailable adapter has an explicit safe landing inside its allowed tabs', () => {
  const unavailable = structuredClone(profiledPortfolioAdmin)
  unavailable.scope.items[1].operations_profile = {
    default_page: 'dashboard',
    data_adapter: 'unavailable',
    available_tabs: ['dashboard']
  }
  unavailable.scope.effective_capabilities = []
  unavailable.role = 'viewer'
  const scope = { type: 'building', code: 'DEMO_B' }
  const allowed = allowedTabsForWorkspace(unavailable, WORKSPACE_OPERATIONS, scope)
  const resolved = resolveWorkspaceTab(unavailable, WORKSPACE_OPERATIONS, 'contracts', scope)

  assert.deepEqual(allowed, ['dashboard'])
  assert.equal(resolved, 'dashboard')
  assert.equal(allowed.includes(resolved), true)
})
