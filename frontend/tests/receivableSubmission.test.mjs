import assert from 'node:assert/strict'
import test from 'node:test'
import { createPaymentOperationId, createReceivableSubmission, receivableSubmissionFor, releaseReceivableSubmission } from '../src/lib/receivableSubmission.js'

const id = '26b40162-a9ab-44f2-bf57-49a2a87339a0'
const input = () => ({ contractId: 7, buildingCode: 'DEMO_A', month: '2026-09',
  form: { payment_amount: 100 }, metadata: { month: '2026-09' },
  body: { month: '2026-09', amount: 100, metadata: { month: '2026-09', notes: 'original' } } })
const receipt = body => ({ operation_id: body.operation_id, contract_id: 7, payment_id: 21, status: 'recorded' })
const failure = status => Object.assign(new Error('synthetic failure'), { status })

test('rapid duplicate saves share one POST, including after acknowledgement', async () => {
  let finish
  const calls = []
  const submitter = createReceivableSubmission((path, options) => {
    calls.push({ path, options })
    return new Promise(resolve => { finish = () => resolve(receipt(JSON.parse(options.body))) })
  }, () => id)
  const saving = submitter.submit(input())
  assert.equal(submitter.state.saving, true)
  assert.equal(await submitter.submit(input()), null)
  finish()
  await saving
  assert.equal(await submitter.submit(input()), null)
  assert.equal(calls.length, 1)
  assert.match(calls[0].path, /building=DEMO_A$/)
  assert.equal(calls[0].options.includeScope, false)
})

test('lost response is reconciled by receipt without sending another POST', async () => {
  const calls = []
  const submitter = createReceivableSubmission(async (path, options) => {
    calls.push(options.method || 'GET')
    if (options.method === 'POST') throw failure(503)
    return receipt({ operation_id: id })
  }, () => id)
  await assert.rejects(submitter.submit(input()))
  assert.equal(submitter.state.uncertain, true)
  assert.deepEqual(submitter.pendingForm(), input().form)
  assert.equal((await submitter.submit(input())).status, 'recorded')
  assert.deepEqual(calls, ['POST', 'GET'])
  assert.equal(submitter.state.uncertain, false)
})

test('missing receipt retries original ID, scope, month and frozen content', async () => {
  const posts = []
  const paths = []
  const submitter = createReceivableSubmission(async (path, options) => {
    paths.push(path)
    if (options.method !== 'POST') throw failure(404)
    posts.push(JSON.parse(options.body))
    if (posts.length === 1) throw failure()
    return receipt(posts.at(-1))
  }, () => id)
  const original = input()
  await assert.rejects(submitter.submit(original))
  original.body.amount = 900
  original.body.metadata.notes = 'changed while in flight'
  original.buildingCode = 'OTHER'
  await submitter.submit(original)
  assert.deepEqual(posts[0], posts[1])
  assert.equal(posts[1].amount, 100)
  assert.ok(paths.every(path => path.endsWith('building=DEMO_A')))
})

test('validation rejection permits editing; query failures never cause blind re-POST', async () => {
  const rejected = createReceivableSubmission(async () => { throw failure(422) }, () => id)
  await assert.rejects(rejected.submit(input()))
  assert.equal(rejected.state.uncertain, false)
  assert.equal(rejected.pendingForm(), null)
  const calls = []
  const unavailable = createReceivableSubmission(async (path, options) => {
    calls.push(options.method || 'GET')
    throw failure(503)
  }, () => id)
  await assert.rejects(unavailable.submit(input()))
  await assert.rejects(unavailable.submit(input()))
  assert.deepEqual(calls, ['POST', 'GET'])
  assert.equal(unavailable.state.uncertain, true)
})

test('deleted payment receipt never recreates a payment', async () => {
  let posts = 0
  const submitter = createReceivableSubmission(async (path, options) => {
    if (options.method === 'POST') { posts++; throw failure() }
    return { ...receipt({ operation_id: id }), payment_id: null, status: 'deleted' }
  }, () => id)
  await assert.rejects(submitter.submit(input()))
  assert.equal((await submitter.submit(input())).status, 'deleted')
  assert.equal(posts, 1)
})

test('unexpected legacy response does not claim a successful keyed save', async () => {
  const submitter = createReceivableSubmission(async () => ({ rows: [] }), () => id)
  await assert.rejects(submitter.submit(input()))
  assert.equal(submitter.state.uncertain, true)
})

test('metadata-only save does not generate a payment', async () => {
  const methods = []
  const submitter = createReceivableSubmission(async (path, options) => { methods.push(options.method); return { rows: [] } })
  assert.equal((await submitter.submit({ ...input(), body: null })).status, 'metadata_saved')
  assert.deepEqual(methods, ['PATCH'])
})

test('uncertain submissions survive remounts but cannot cross account or building', async () => {
  const request = async () => { throw failure() }
  const first = receivableSubmissionFor('accountA:DEMO_A:7:2026-09', request)
  await assert.rejects(first.submit(input()))
  releaseReceivableSubmission('accountA:DEMO_A:7:2026-09')
  assert.equal(receivableSubmissionFor('accountA:DEMO_A:7:2026-09', request), first)
  assert.notEqual(receivableSubmissionFor('accountB:DEMO_A:7:2026-09', request), first)
  assert.notEqual(receivableSubmissionFor('accountA:OTHER:7:2026-09', request), first)
})

test('operation IDs work on HTTP origins without randomUUID', () => {
  const crypto = { getRandomValues: bytes => { bytes.fill(255); return bytes } }
  assert.match(createPaymentOperationId(crypto), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/)
})

test('a stalled request becomes uncertain after the deadline and reconciles before retrying', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const calls = []
  const submitter = createReceivableSubmission((path, options) => {
    calls.push(options.method || 'GET')
    if (options.method !== 'POST') return Promise.resolve(receipt({ operation_id: id }))
    return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Timed out', 'AbortError')), { once: true })
    })
  }, () => id)
  const pending = submitter.submit(input())
  const rejected = assert.rejects(pending, { name: 'AbortError' })
  t.mock.timers.tick(30_000)
  await rejected
  assert.equal(submitter.state.saving, false)
  assert.equal(submitter.state.uncertain, true)
  await submitter.submit(input())
  assert.deepEqual(calls, ['POST', 'GET'])
})
