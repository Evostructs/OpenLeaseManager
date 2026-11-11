import { watch } from 'vue'

// Form edits and contract edits have separate dependencies. Including the full
// contract source tree in a deep form watcher makes every slider input traverse
// all contracts and all their monthly source records.
export function watchPlanningDraftChanges({ state, adjustments, planName }, onChange) {
  const stopForm = watch(() => [
    state.value.scenario, state.value.strategy,
    state.value.leaseOwner, state.value.savingOwner, planName.value
  ], onChange, { deep: true, flush: 'sync' })
  const stopContracts = watch(adjustments, onChange, { flush: 'sync' })
  return () => { stopForm(); stopContracts() }
}
