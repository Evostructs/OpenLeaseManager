import assert from 'node:assert/strict'

import {
  aiHighlights,
  aiMainText,
  aiResultText,
  aiSuggestedActions
} from '../src/lib/aiAssistText.js'

const risk = {
  explanation: `{
    "explanation":"合同 DEMO_A-2026-118 将在 51 天内到期，需确认续租、退租或变更安排。",
    "highlights":["到期日：2026-07-06，剩余 51 天","押金余额：57,381.66 元"],
    "suggested_actions":["联系承租人确认意向","提前准备续租合同或退租验收流程"],
  }`
}

assert.match(aiMainText(risk, 'explanation'), /将在 51 天内到期/)
assert.doesNotMatch(aiMainText(risk, 'explanation'), /"explanation"/)
assert.doesNotMatch(aiResultText(risk, 'explanation'), /{/)
assert.ok(aiHighlights(risk, 'explanation').includes('押金余额：57,381.66 元'))
assert.ok(aiSuggestedActions(risk, 'explanation').includes('联系承租人确认意向'))

const summary = {
  summary: '```json\n{"summary":"客户经营稳定，近期需要确认续租意向。","highlights":["合同正常履行"],"suggested_actions":["确认联系人"]}\n```'
}
assert.equal(aiMainText(summary, 'summary'), '客户经营稳定，近期需要确认续租意向。')

const suggestion = {
  suggestion: '{"summary":"请电话确认续租意向。","follow_up_points":["确认联系人","约定下次沟通"]}'
}
assert.equal(aiMainText(suggestion, 'suggestion'), '请电话确认续租意向。')
assert.equal(aiSuggestedActions(suggestion, 'suggestion').join('\n'), '确认联系人\n约定下次沟通')
