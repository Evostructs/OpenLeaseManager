import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canSubmitPreflight,
  preflightStatusLabel,
  receiptGuidance,
  receiptStatusLabel,
  uploadErrorsFromFailure
} from '../src/lib/snapshotExchangeDomain.js'


test('preflight gates formal submission without treating accepted as published', () => {
  assert.equal(canSubmitPreflight({ valid: true, would_status: 'accepted' }), true)
  assert.equal(canSubmitPreflight({ valid: true, would_status: 'duplicate' }), true)
  assert.equal(canSubmitPreflight({ valid: false, would_status: 'rejected' }), false)
  assert.equal(receiptStatusLabel('accepted'), '已接收')
  assert.equal(preflightStatusLabel('accepted'), '可接收')
  assert.match(receiptGuidance({ status: 'accepted', center_status: 'pending_review' }), /待中心复核/)
  assert.doesNotMatch(receiptGuidance({ status: 'accepted', center_status: 'pending_review' }), /已经发布/)
})


test('upload failures expose only the stable safe issue list', () => {
  const errors = uploadErrorsFromFailure({
    payload: {
      errors: [
        { code: 'excel_formula_not_allowed', field: 'Metrics!B2', row: 2, message: 'Excel 不允许公式' }
      ],
      detail: 'ignored transport detail'
    }
  })
  assert.deepEqual(errors, [
    { code: 'excel_formula_not_allowed', field: 'Metrics!B2', row: 2, message: 'Excel 不允许公式' }
  ])
  assert.deepEqual(uploadErrorsFromFailure({ payload: { receipt: { errors } } }), errors)
})
