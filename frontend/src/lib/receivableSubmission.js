import { reactive } from 'vue'

// LAN deployments can use HTTP, where randomUUID is unavailable.
export function createPaymentOperationId(crypto = globalThis.crypto) {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function createReceivableSubmission(request, createId = createPaymentOperationId, { timeoutMs = 30_000 } = {}) {
  const state = reactive({ saving: false, uncertain: false, receipt: null, error: '' })
  let pending = null
  const clone = value => JSON.parse(JSON.stringify(value))
  const path = operation => `/api/contracts/receivables/monthly/${operation.contractId}`
  const call = async (operation, suffix, options = {}) => {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      return await request(
        `${path(operation)}${suffix}?${new URLSearchParams({ building: operation.buildingCode })}`,
        { ...options, signal: controller.signal, includeScope: false },
      )
    } finally {
      clearTimeout(timer)
    }
  }
  const validateReceipt = (receipt, operation) => {
    if (receipt?.operation_id !== operation.body.operation_id
      || receipt?.contract_id !== operation.contractId
      || !['recorded', 'deleted'].includes(receipt?.status)) {
      throw new Error('未收到有效收款回执，请核对本次结果')
    }
    return receipt
  }
  return {
    state,
    pendingForm: () => pending ? clone(pending.form) : null,
    async submit(input) {
      if (state.saving || state.receipt) return null
      state.saving = true
      state.error = ''
      try {
        if (!pending) {
          pending = clone(input)
          if (pending.body) pending.body.operation_id = createId()
        }
        const operation = pending
        let receipt
        if (state.uncertain && operation.body) {
          try {
            receipt = validateReceipt(await call(operation,
              `/payment-operations/${operation.body.operation_id}`), operation)
          } catch (error) {
            if (error.status !== 404) throw error
            // Not found does not prove the original request stopped. Resubmit
            // the identical operation so the server arbitrates any late arrival.
          }
        }
        if (!receipt) {
          if (operation.body) {
            receipt = validateReceipt(await call(operation, '/payment-operations', {
              method: 'POST', body: JSON.stringify(operation.body),
            }), operation)
          } else {
            await call(operation, '', { method: 'PATCH', body: JSON.stringify(operation.metadata) })
            receipt = { status: 'metadata_saved' }
          }
        }
        state.receipt = receipt
        state.uncertain = false
        pending = null
        return receipt
      } catch (error) {
        // A received validation/permission rejection means nothing was written.
        // Network/5xx and conflicts need reconciliation with the original key.
        const rejected = [400, 401, 403, 404, 422].includes(error.status)
        if (!state.uncertain && rejected) pending = null
        else state.uncertain = true
        state.error = state.uncertain
          ? '暂未确认保存结果，请点击“核对并重试”。本次内容已保留，重试不会重复记账。'
          : error.message
        throw error
      } finally {
        state.saving = false
      }
    },
  }
}

// Keep unresolved operations across view remounts, separated by account, building,
// contract and month. No credentials or financial drafts are written to storage.
const unresolved = new Map()
export function receivableSubmissionFor(key, request) {
  const previous = unresolved.get(key)
  if (previous?.state.saving || previous?.state.uncertain) return previous
  const submission = createReceivableSubmission(request)
  unresolved.set(key, submission)
  return submission
}

export function releaseReceivableSubmission(key) {
  const submission = unresolved.get(key)
  if (submission && !submission.state.saving && !submission.state.uncertain) unresolved.delete(key)
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', event => {
    if (![...unresolved.values()].some(item => item.state.saving || item.state.uncertain)) return
    event.preventDefault()
    event.returnValue = ''
  })
}
