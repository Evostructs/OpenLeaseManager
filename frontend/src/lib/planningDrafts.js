// Page-memory drafts only: never write financial data or credentials to browser
// storage. Identity/authorization changes clear all retained drafts immediately.
export function planningIdentity(user) {
  if (!user?.id && !user?.username) return ''
  return JSON.stringify([user.id || '', user.username || '', user.role || '',
    user.scope?.authorization_version ?? user.authorization_version ?? '',
    user.scope?.gate_policy_version || '', user.scope?.items || []])
}

export function createPlanningDraftStore({ onPending = () => {}, limit = 20 } = {}) {
  let identity = ''
  const drafts = new Map()
  const editors = new Map()
  const clone = value => JSON.parse(JSON.stringify(value))
  const notify = () => onPending(drafts.size > 0 || [...editors.values()].some(Boolean))
  const key = (building, year) => JSON.stringify([building, Number(year)])
  return {
    identify(next) {
      if (next === identity) return
      identity = next
      drafts.clear()
      editors.clear()
      notify()
    },
    save(owner, building, year, value) {
      if (!owner || owner !== identity) throw new Error('登录或权限已变化，请重新进入当前项目。')
      const slot = key(building, year)
      if (!drafts.has(slot) && drafts.size >= limit) throw new Error('本页暂存草稿已满，请先保存或处理其他年度的草稿。')
      drafts.set(slot, clone(value))
      notify()
    },
    get(owner, building, year) {
      const value = owner && owner === identity ? drafts.get(key(building, year)) : null
      return value ? clone(value) : null
    },
    remove(owner, building, year) {
      if (owner === identity) drafts.delete(key(building, year))
      notify()
    },
    has(owner) { return Boolean(owner && owner === identity && drafts.size) },
    pending(editor, value) { editors.set(editor, Boolean(value)); notify() },
    release(editor) { editors.delete(editor); notify() },
  }
}

const beforeUnload = event => { event.preventDefault(); event.returnValue = '' }
let listening = false
export const planningDrafts = createPlanningDraftStore({ onPending(pending) {
  if (typeof window === 'undefined' || pending === listening) return
  listening = pending
  if (pending) window.addEventListener('beforeunload', beforeUnload)
  else window.removeEventListener('beforeunload', beforeUnload)
} })
