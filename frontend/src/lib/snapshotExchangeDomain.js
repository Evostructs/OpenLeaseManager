const STATUS_LABELS = Object.freeze({
  accepted: '已接收',
  duplicate: '重复内容',
  rejected: '未通过',
  conflict: '投递冲突'
})

export function receiptStatusLabel(status) {
  return STATUS_LABELS[String(status || '')] || '未知状态'
}

export function preflightStatusLabel(status) {
  return {
    accepted: '可接收',
    duplicate: '将判为重复',
    rejected: '预检未通过',
    conflict: '预计冲突'
  }[String(status || '')] || '待预检'
}

export function canSubmitPreflight(preflight) {
  return Boolean(
    preflight?.valid
    && ['accepted', 'duplicate'].includes(String(preflight?.would_status || ''))
  )
}

export function receiptGuidance(receipt = {}) {
  if (receipt.status === 'accepted') {
    return '待中心复核，发布后更新经营概况。'
  }
  if (receipt.status === 'duplicate') {
    return '该内容已提交，请查看原回执。'
  }
  if (receipt.status === 'conflict') {
    return '投递号或修订记录冲突，请核对原记录。'
  }
  if (receipt.status === 'rejected') {
    return '请按错误清单修正原文件，再次预检；通信重试必须继续使用同一投递号。'
  }
  return '请刷新回执确认最终状态；响应丢失时使用原文件和原投递号重试。'
}

export function uploadErrorsFromFailure(error = {}) {
  const direct = error?.payload?.errors
  if (Array.isArray(direct)) return direct
  const receiptErrors = error?.payload?.receipt?.errors
  if (Array.isArray(receiptErrors)) return receiptErrors
  return [{
    code: 'upload_failed',
    field: null,
    row: null,
    message: error?.message || '文件处理失败，请检查后重试'
  }]
}

export function compactExchangeRef(value, head = 10, tail = 8) {
  const text = String(value || '')
  if (!text) return '--'
  if (text.length <= head + tail + 1) return text
  return `${text.slice(0, head)}…${text.slice(-tail)}`
}

export function formatExchangeTime(value) {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(date)
}
