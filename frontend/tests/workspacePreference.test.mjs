import assert from 'node:assert/strict'
import test from 'node:test'

import {
  readWorkspacePreference,
  writeWorkspacePreference
} from '../src/lib/workspacePreference.js'

const mixedWorkspaceUser = {
  id: 42,
  username: 'hybrid_workspace',
  role: 'editor',
  scope: {
    authorization_version: 'scope-v1-abc',
    default: {
      code: 'DEMO_A',
      operations_profile: {
        default_page: 'dashboard',
        data_adapter: 'live_detail',
        available_tabs: ['dashboard', 'contracts', 'customers']
      }
    },
    items: [{
      code: 'DEMO_A',
      access_level: 'edit',
      operations_profile: {
        default_page: 'dashboard',
        data_adapter: 'live_detail',
        available_tabs: ['dashboard', 'contracts', 'customers']
      }
    }],
    portfolio: { items: [] },
    portfolio_overview: {
      items: [{ code: 'DEMO_OFFICE_PORTFOLIO' }]
    },
    effective_capabilities: []
  }
}

test('a valid same-account preference restores the default workspace and both last pages', () => {
  const storage = {
    getItem(key) {
      assert.equal(key, 'lease_workspace_preference:v1:42')
      return JSON.stringify({
        schema_version: 1,
        user_id: '42',
        username: 'hybrid_workspace',
        authorization_version: 'scope-v1-abc',
        default_workspace: 'operations',
        last_tabs: {
          leadership: 'overview',
          operations: 'contracts'
        }
      })
    }
  }

  assert.deepEqual(readWorkspacePreference(mixedWorkspaceUser, storage), {
    preferredWorkspace: 'operations',
    preferredLastTabs: {
      leadership: 'overview',
      operations: 'contracts'
    }
  })
})

test('an authorization change discards the exact stale account preference', () => {
  const removed = []
  const storage = {
    getItem() {
      return JSON.stringify({
        schema_version: 1,
        user_id: '42',
        username: 'hybrid_workspace',
        authorization_version: 'scope-v1-stale',
        default_workspace: 'operations',
        last_tabs: { operations: 'contracts' }
      })
    },
    removeItem(key) {
      removed.push(key)
    }
  }

  assert.equal(readWorkspacePreference(mixedWorkspaceUser, storage), null)
  assert.deepEqual(removed, ['lease_workspace_preference:v1:42'])
})

test('malformed JSON and an old schema are discarded without affecting login', () => {
  for (const storedValue of [
    '{not-json',
    JSON.stringify({
      schema_version: 0,
      user_id: '42',
      username: 'hybrid_workspace',
      authorization_version: 'scope-v1-abc',
      default_workspace: 'operations',
      last_tabs: { leadership: 'overview', operations: 'contracts' }
    })
  ]) {
    const removed = []
    const storage = {
      getItem() {
        return storedValue
      },
      removeItem(key) {
        removed.push(key)
      }
    }

    assert.equal(readWorkspacePreference(mixedWorkspaceUser, storage), null)
    assert.deepEqual(removed, ['lease_workspace_preference:v1:42'])
  }
})

test('saving a preference persists only account identity, authorization version, workspace and page keys', () => {
  const writes = []
  const storage = {
    setItem(key, value) {
      writes.push([key, JSON.parse(value)])
    }
  }

  assert.equal(writeWorkspacePreference(mixedWorkspaceUser, {
    activeWorkspace: 'operations',
    lastTabs: {
      leadership: 'overview',
      operations: 'customers'
    },
    selectedBuilding: 'DEMO_A',
    routeQuery: { building: 'DEMO_A' }
  }, storage), true)
  assert.deepEqual(writes, [[
    'lease_workspace_preference:v1:42',
    {
      schema_version: 1,
      user_id: '42',
      username: 'hybrid_workspace',
      authorization_version: 'scope-v1-abc',
      default_workspace: 'operations',
      last_tabs: {
        leadership: 'overview',
        operations: 'customers'
      }
    }
  ]])
})

test('invalid workspace or page keys discard the untrusted preference instead of being repaired', () => {
  for (const invalidFields of [
    { default_workspace: 'unknown', last_tabs: { leadership: 'overview', operations: 'contracts' } },
    { default_workspace: 'operations', last_tabs: { leadership: 'overview', operations: 'tools' } }
  ]) {
    const removed = []
    const storage = {
      getItem() {
        return JSON.stringify({
          schema_version: 1,
          user_id: '42',
          username: 'hybrid_workspace',
          authorization_version: 'scope-v1-abc',
          ...invalidFields
        })
      },
      removeItem(key) {
        removed.push(key)
      }
    }

    assert.equal(readWorkspacePreference(mixedWorkspaceUser, storage), null)
    assert.deepEqual(removed, ['lease_workspace_preference:v1:42'])
  }
})

test('preferences for two accounts in the same browser never share a storage record', () => {
  const values = new Map()
  const storage = {
    getItem(key) {
      return values.get(key) || null
    },
    setItem(key, value) {
      values.set(key, value)
    }
  }
  const otherUser = {
    ...mixedWorkspaceUser,
    id: 43,
    username: 'other_hybrid_workspace',
    scope: {
      ...mixedWorkspaceUser.scope,
      authorization_version: 'scope-v1-def'
    }
  }

  assert.equal(writeWorkspacePreference(mixedWorkspaceUser, {
    activeWorkspace: 'operations',
    lastTabs: { leadership: 'overview', operations: 'contracts' }
  }, storage), true)
  assert.equal(writeWorkspacePreference(otherUser, {
    activeWorkspace: 'leadership',
    lastTabs: { leadership: 'overview', operations: 'customers' }
  }, storage), true)
  assert.deepEqual([...values.keys()].sort(), [
    'lease_workspace_preference:v1:42',
    'lease_workspace_preference:v1:43'
  ])
  assert.equal(readWorkspacePreference(mixedWorkspaceUser, storage).preferredWorkspace, 'operations')
  assert.equal(readWorkspacePreference(otherUser, storage).preferredWorkspace, 'leadership')
})

test('browser storage failures fall back safely without blocking authentication', () => {
  const unreadableStorage = {
    getItem() {
      throw new Error('blocked')
    },
    removeItem() {
      throw new Error('blocked')
    }
  }
  const unwritableStorage = {
    setItem() {
      throw new Error('quota')
    }
  }

  assert.equal(readWorkspacePreference(mixedWorkspaceUser, unreadableStorage), null)
  assert.equal(writeWorkspacePreference(mixedWorkspaceUser, {
    activeWorkspace: 'operations',
    lastTabs: { leadership: 'overview', operations: 'contracts' }
  }, unwritableStorage), false)
})

test('a remembered page removed by the default building profile is discarded as stale', () => {
  const snapshotUser = {
    ...mixedWorkspaceUser,
    scope: {
      ...mixedWorkspaceUser.scope,
      default: {
        code: 'DEMO_B',
        operations_profile: {
          default_page: 'evidence',
          data_adapter: 'published_snapshot',
          available_tabs: ['dashboard', 'evidence', 'exchange']
        }
      },
      items: [{
        code: 'DEMO_B',
        access_level: 'edit',
        operations_profile: {
          default_page: 'evidence',
          data_adapter: 'published_snapshot',
          available_tabs: ['dashboard', 'evidence', 'exchange']
        }
      }]
    }
  }
  const removed = []
  const storage = {
    getItem() {
      return JSON.stringify({
        schema_version: 1,
        user_id: '42',
        username: 'hybrid_workspace',
        authorization_version: 'scope-v1-abc',
        default_workspace: 'operations',
        last_tabs: { leadership: 'overview', operations: 'contracts' }
      })
    },
    removeItem(key) {
      removed.push(key)
    }
  }

  assert.equal(readWorkspacePreference(snapshotUser, storage), null)
  assert.deepEqual(removed, ['lease_workspace_preference:v1:42'])
})

test('saving a removed page repairs it to the default building profile landing page', () => {
  const snapshotUser = {
    ...mixedWorkspaceUser,
    scope: {
      ...mixedWorkspaceUser.scope,
      default: {
        code: 'DEMO_B',
        operations_profile: {
          default_page: 'evidence',
          data_adapter: 'published_snapshot',
          available_tabs: ['dashboard', 'evidence', 'exchange']
        }
      },
      items: [{
        code: 'DEMO_B',
        access_level: 'edit',
        operations_profile: {
          default_page: 'evidence',
          data_adapter: 'published_snapshot',
          available_tabs: ['dashboard', 'evidence', 'exchange']
        }
      }]
    }
  }
  const writes = []
  const storage = {
    setItem(key, value) {
      writes.push([key, JSON.parse(value)])
    }
  }

  assert.equal(writeWorkspacePreference(snapshotUser, {
    activeWorkspace: 'operations',
    lastTabs: { leadership: 'overview', operations: 'contracts' }
  }, storage), true)
  assert.equal(writes[0][1].last_tabs.operations, 'evidence')
})
