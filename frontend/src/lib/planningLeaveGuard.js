import { t } from '../i18n/index.js'
import { reactive } from 'vue'

// One pending choice owns the navigation. A second click cannot replace its
// destination, and a permission change cancels it without delaying revocation.
export function createPlanningLeaveGuard({ changed, busy, save, discard }) {
  const state = reactive({ open: false, label: '', resolving: false, error: '', allowSave: true })
  let resolvePending = null
  let generation = 0
  const cancel = () => {
    generation++
    resolvePending?.(false)
    resolvePending = null
    Object.assign(state, { open: false, resolving: false, error: '' })
  }
  return {
    state, cancel,
    request(label, { force = false, allowSave = true } = {}) {
      if (busy() || resolvePending) return Promise.resolve(false)
      if (!force && !changed()) return Promise.resolve(true)
      Object.assign(state, { open: true, label, error: '', allowSave })
      return new Promise(resolve => { resolvePending = resolve })
    },
    async choose(choice) {
      if (!resolvePending || state.resolving) return
      if (choice === 'stay') { cancel(); return }
      if (!['save', 'discard'].includes(choice)) return
      if (choice === 'save' && !state.allowSave) return
      const current = generation
      state.resolving = true
      state.error = ''
      try {
        if (choice === 'save') await save()
        else await discard()
        if (current !== generation) return
        const finish = resolvePending
        resolvePending = null
        state.open = false
        finish?.(true)
      } catch (error) {
        if (current === generation) state.error = error?.message || '草稿暂存失败，请继续编辑。'
      } finally {
        if (current === generation) state.resolving = false
      }
    },
  }
}

// A parked draft can outlive its editor (overview page, remount or read loss).
export function confirmPlanningLeaveWithoutEditor(options = {}) {
  return !(options.force && options.allowSave === false) || window.confirm(t(
    '退出登录会清除本页所有暂存财务草稿。确定放弃草稿并退出吗？'))
}
