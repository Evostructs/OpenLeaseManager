import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, ref } from 'vue'
import { watchPlanningDraftChanges } from '../src/lib/planningDraftWatch.js'
import { createPlanningScenario } from '../src/lib/financialPlanningDomain.js'

test('slider edits remain independent of contract source size and still mark drafts dirty', () => {
  let sourceReads = 0
  const state = ref({ scenario: createPlanningScenario(2026), strategy: {}, leaseOwner: '', savingOwner: '' })
  const planName = ref('plan')
  const contracts = ref(Array.from({ length: 200 }, (_, index) => ({
    contract_id: index + 1, start_shift_months: 0, rent_change_percent: 0,
    get source_snapshot() { sourceReads += 1; return { months: Array.from({ length: 12 }, () => ({ revenue: 100 })) } }
  })))
  const adjustments = computed(() => contracts.value
    .filter(row => Number(row.start_shift_months) !== 0 || Number(row.rent_change_percent) !== 0)
    .map(row => ({ contract_id: row.contract_id, start_shift_months: Number(row.start_shift_months), rent_change_percent: Number(row.rent_change_percent) })))
  let dirtyEvents = 0
  const stop = watchPlanningDraftChanges({ state, adjustments, planName }, () => { dirtyEvents += 1 })
  for (let value = 1; value <= 24; value += 1) state.value.scenario.newLease.monthlyRent = value
  assert.equal(dirtyEvents, 24)
  assert.equal(sourceReads, 0, 'measure edits must never traverse saved contract source records')
  contracts.value[0].rent_change_percent = 5
  assert.equal(dirtyEvents, 25)
  assert.deepEqual(adjustments.value, [{ contract_id: 1, start_shift_months: 0, rent_change_percent: 5 }])
  planName.value = 'renamed'
  assert.equal(dirtyEvents, 26)
  stop()
})
