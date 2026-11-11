import {
  allowedTabsForWorkspace,
  availableWorkspacesForUser,
  defaultWorkspaceForUser,
  resolveWorkspaceTab
} from './navigationPolicy.js'

const WORKSPACE_PREFERENCE_SCHEMA_VERSION = 1
const WORKSPACE_PREFERENCE_KEY_PREFIX = 'lease_workspace_preference:v1:'

function userIdentity(user = {}) {
  return {
    id: String(user?.id || '').trim(),
    username: String(user?.username || '').trim(),
    authorizationVersion: String(user?.scope?.authorization_version || '').trim()
  }
}

function browserStorage() {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function discardPreference(storage, key) {
  try {
    storage.removeItem(key)
  } catch {}
}

function workspacePreferenceStorageKey(userId) {
  const id = String(userId || '').trim()
  return id ? `${WORKSPACE_PREFERENCE_KEY_PREFIX}${id}` : ''
}

export function readWorkspacePreference(user = {}, storage = browserStorage()) {
  const identity = userIdentity(user)
  const key = workspacePreferenceStorageKey(identity.id)
  if (!storage || !key || !identity.username || !identity.authorizationVersion) return null

  try {
    const raw = storage.getItem(key)
    if (!raw) return null
    const value = JSON.parse(raw)
    if (
      value?.schema_version !== WORKSPACE_PREFERENCE_SCHEMA_VERSION
      || String(value?.user_id || '') !== identity.id
      || String(value?.username || '') !== identity.username
      || String(value?.authorization_version || '') !== identity.authorizationVersion
    ) {
      discardPreference(storage, key)
      return null
    }

    const available = availableWorkspacesForUser(user)
    const lastTabs = value?.last_tabs
    const validLastTabs = lastTabs
      && typeof lastTabs === 'object'
      && !Array.isArray(lastTabs)
      && available.every((workspace) => (
        allowedTabsForWorkspace(user, workspace).includes(lastTabs[workspace])
      ))
    if (!available.includes(value?.default_workspace) || !validLastTabs) {
      discardPreference(storage, key)
      return null
    }

    const preferredWorkspace = value.default_workspace
    const preferredLastTabs = Object.fromEntries(available.map((workspace) => [
      workspace,
      lastTabs[workspace]
    ]))
    return { preferredWorkspace, preferredLastTabs }
  } catch {
    discardPreference(storage, key)
    return null
  }
}

export function writeWorkspacePreference(user = {}, preference = {}, storage = browserStorage()) {
  const identity = userIdentity(user)
  const key = workspacePreferenceStorageKey(identity.id)
  if (!storage || !key || !identity.username || !identity.authorizationVersion) return false

  const available = availableWorkspacesForUser(user)
  const defaultWorkspace = available.includes(preference?.activeWorkspace)
    ? preference.activeWorkspace
    : defaultWorkspaceForUser(user)
  const lastTabs = Object.fromEntries(available.map((workspace) => [
    workspace,
    resolveWorkspaceTab(user, workspace, preference?.lastTabs?.[workspace])
  ]))
  const value = {
    schema_version: WORKSPACE_PREFERENCE_SCHEMA_VERSION,
    user_id: identity.id,
    username: identity.username,
    authorization_version: identity.authorizationVersion,
    default_workspace: defaultWorkspace,
    last_tabs: lastTabs
  }

  try {
    storage.setItem(key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}
