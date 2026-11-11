import assert from 'node:assert/strict'
import test from 'node:test'
import { createPlanningDraftStore, planningIdentity } from '../src/lib/planningDrafts.js'
import { createPlanningLeaveGuard, confirmPlanningLeaveWithoutEditor } from '../src/lib/planningLeaveGuard.js'

test('drafts are cloned and isolated by account, authorization, building and year', () => {
  const store = createPlanningDraftStore()
  const owner = planningIdentity({ id: 1, role: 'admin', scope: { authorization_version: 1 } })
  store.identify(owner)
  const draft = { budget: [10], expenses: [{ amount: 5 }], name: 'unpublished' }
  store.save(owner, 'A', 2026, draft)
  draft.expenses[0].amount = 99
  const restored = store.get(owner, 'A', 2026)
  assert.equal(restored.expenses[0].amount, 5)
  restored.budget[0] = 90
  assert.equal(store.get(owner, 'A', 2026).budget[0], 10)
  assert.equal(store.get(owner, 'B', 2026), null)
  assert.equal(store.get(owner, 'A', 2027), null)
  assert.equal(store.get('other-account', 'A', 2026), null)
  store.identify(planningIdentity({ id: 1, role: 'viewer', scope: { authorization_version: 2 } }))
  assert.equal(store.get(owner, 'A', 2026), null)
  assert.throws(() => store.save(owner, 'A', 2026, draft), /权限已变化/)
})

test('closing the editor still warns about parked drafts; saving, discarding or logout clears pending state', () => {
  const pending = []
  const store = createPlanningDraftStore({ onPending: value => pending.push(value) })
  store.identify('A')
  store.pending('editor', true)
  store.save('A', 'building', 2026, { invalidBudget: '', unsavedExpense: true })
  store.release('editor')
  assert.equal(pending.at(-1), true)
  store.remove('A', 'building', 2026)
  assert.equal(pending.at(-1), false)
  store.pending('editor', true)
  store.identify('')
  assert.equal(pending.at(-1), false)
})

test('draft capacity never silently evicts a previously saved draft', () => {
  const store = createPlanningDraftStore({ limit: 1 })
  store.identify('A')
  store.save('A', 'B', 2026, { name: 'retain' })
  assert.throws(() => store.save('A', 'B', 2027, {}), /已满/)
  assert.deepEqual(store.get('A', 'B', 2026), { name: 'retain' })
})

test('continue editing and duplicate navigation clicks do not mutate or replace the draft', async () => {
  const actions = []
  const guard = createPlanningLeaveGuard({ changed: () => true, busy: () => false,
    save: () => actions.push('save'), discard: () => actions.push('discard') })
  const first = guard.request('year')
  assert.equal(await guard.request('building'), false)
  assert.equal(guard.state.label, 'year')
  await guard.choose('stay')
  assert.equal(await first, false)
  assert.deepEqual(actions, [])
})

test('only an explicit save or discard permits leaving', async () => {
  const actions = []
  const guard = createPlanningLeaveGuard({ changed: () => true, busy: () => false,
    save: () => actions.push('memory-draft'), discard: () => actions.push('discard') })
  const save = guard.request('exit')
  await guard.choose('save')
  assert.equal(await save, true)
  const discard = guard.request('history')
  await guard.choose('discard')
  assert.equal(await discard, true)
  assert.deepEqual(actions, ['memory-draft', 'discard'])
})

test('draft save failure keeps the choice open; permission loss cancels a pending operation', async () => {
  let finish
  let fail = true
  const guard = createPlanningLeaveGuard({ changed: () => true, busy: () => false,
    save: async () => { if (fail) throw new Error('draft unavailable'); await new Promise(resolve => { finish = resolve }) }, discard: () => {} })
  const pending = guard.request('exit')
  await guard.choose('save')
  assert.equal(guard.state.open, true)
  assert.equal(guard.state.error, 'draft unavailable')
  fail = false
  const saving = guard.choose('save')
  guard.cancel()
  finish()
  await saving
  assert.equal(await pending, false)
  assert.equal(guard.state.open, false)
})

test('a clean editor leaves immediately, while a write in progress prevents navigation', async () => {
  let busy = false
  const guard = createPlanningLeaveGuard({ changed: () => false, busy: () => busy, save: () => {}, discard: () => {} })
  assert.equal(await guard.request('exit'), true)
  busy = true
  assert.equal(await guard.request('exit'), false)
  assert.equal(guard.state.open, false)
})

test('logout cannot stash a draft that would be cleared by leaving the account', async () => {
  const actions = []
  const guard = createPlanningLeaveGuard({ changed: () => false, busy: () => false,
    save: () => actions.push('save'), discard: () => actions.push('discard') })
  const pending = guard.request('logout', { force: true, allowSave: false })
  await guard.choose('save')
  assert.equal(guard.state.open, true)
  assert.deepEqual(actions, [])
  await guard.choose('stay')
  assert.equal(await pending, false)
})

test('parked drafts still require a choice after their editor has unmounted', () => {
  const previous = globalThis.window
  let prompts = 0
  globalThis.window = { confirm: () => { prompts++; return false } }
  try {
    assert.equal(confirmPlanningLeaveWithoutEditor(), true)
    assert.equal(confirmPlanningLeaveWithoutEditor({ force: false, allowSave: false }), true)
    assert.equal(confirmPlanningLeaveWithoutEditor({ force: true, allowSave: false }), false)
    assert.equal(prompts, 1)
    globalThis.window.confirm = () => true
    assert.equal(confirmPlanningLeaveWithoutEditor({ force: true, allowSave: false }), true)
  } finally { globalThis.window = previous }
})
