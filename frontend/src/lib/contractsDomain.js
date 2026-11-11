export function createContractDomain({ activeStatuses, closedStatuses }) {
  function isExpiringSoon(row) {
    const days = Number(row?.days_to_expiry)
    return Number.isFinite(days) && days >= 0 && days <= 90 && !closedStatuses.has(row?.status)
  }

  function shouldFlagMissingPdf(row) {
    return !row?.pdf_path && (activeStatuses.has(row?.status) || row?.status === '待生效')
  }

  function expiryLabel(row) {
    const days = Number(row?.days_to_expiry)
    if (!Number.isFinite(days)) return '未标注'
    if (days < 0) return `逾期 ${Math.abs(days)} 天`
    if (days === 0) return '今日到期'
    if (days <= 90) return `${days} 天到期`
    return `${days} 天`
  }

  function expiryTone(row) {
    const days = Number(row?.days_to_expiry)
    if (!Number.isFinite(days)) return 'info'
    if (days < 0) return 'error'
    if (days <= 30) return 'warning'
    if (days <= 90) return 'info'
    return 'success'
  }

  function depositAttentionTag(row) {
    const summary = row?.deposit_summary || {}
    if (Number(summary.unpaid_amount || 0) > 0 || Number(summary.unpaid_count || 0) > 0) return '押金未收'
    if (Number(summary.refund_pending_amount || 0) > 0 || Number(summary.refund_pending_count || 0) > 0) return '押金待退'
    if (Number(summary.insufficient_amount || 0) > 0 || Number(summary.insufficient_count || 0) > 0) return '押金不足'
    if (Number(row?.deposit || 0) > 0 && row?.deposit_status && row.deposit_status !== '已收') return row.deposit_status
    return ''
  }

  function contractAttentionTags(row) {
    const tags = []
    const days = Number(row?.days_to_expiry)
    if (Number.isFinite(days) && days < 0 && !closedStatuses.has(row?.status)) tags.push('已过期')
    else if (isExpiringSoon(row)) tags.push(expiryLabel(row))
    if (shouldFlagMissingPdf(row)) tags.push('缺合同 PDF')
    const depositTag = depositAttentionTag(row)
    if (depositTag) tags.push(depositTag)
    if (row?.disposal) tags.push(row.disposal)
    return tags
  }

  function contractNextAction(row) {
    const days = Number(row?.days_to_expiry)
    const depositTag = depositAttentionTag(row)
    if (depositTag) return { label: '押金', detail: depositTag, tone: 'warning' }
    if ((Number.isFinite(days) && days < 0 && !closedStatuses.has(row?.status)) || row?.status === '逾期占用') {
      return { label: '退租', detail: expiryLabel(row), tone: 'danger' }
    }
    if (isExpiringSoon(row)) return { label: '续签', detail: expiryLabel(row), tone: 'warning' }
    if (row?.status === '待生效') return { label: '跟进', detail: '交付', tone: 'info' }
    if (row?.status === '已到期') return { label: '退租', detail: row?.disposal || '已到期', tone: 'muted' }
    if (row?.status === '已变更') return { label: '变更', detail: row?.disposal || '已变更', tone: 'info' }
    if (row?.status === '已退租') return { label: '归档', detail: row?.disposal || '已退租', tone: 'muted' }
    if (row?.disposal) return { label: '跟进', detail: row.disposal, tone: 'info' }
    if (activeStatuses.has(row?.status)) return { label: '变更', detail: '履约中', tone: 'success' }
    return { label: '跟进', detail: row?.status || '待判断', tone: 'info' }
  }

  function contractActionPriority(row, action) {
    const days = Number(row?.days_to_expiry)
    if (depositAttentionTag(row)) return 120
    if (row?.status === '逾期占用') return 115
    if (Number.isFinite(days) && days < 0 && !closedStatuses.has(row?.status)) return 110
    if (Number.isFinite(days) && days >= 0 && days <= 30 && !closedStatuses.has(row?.status)) return 100
    if (isExpiringSoon(row)) return 90
    if (shouldFlagMissingPdf(row)) return 70
    if (row?.status === '待生效') return 60
    if (row?.disposal) return 50
    if (action?.label === '退租') return 45
    if (activeStatuses.has(row?.status)) return 20
    return 10
  }

  function contractActionReason(row, action) {
    const tags = contractAttentionTags(row)
    if (tags.length) return tags.slice(0, 2).join(' / ')
    return action?.detail || row?.status || '待判断'
  }

  return {
    contractActionPriority,
    contractActionReason,
    contractAttentionTags,
    contractNextAction,
    depositAttentionTag,
    expiryLabel,
    expiryTone,
    isExpiringSoon,
    shouldFlagMissingPdf
  }
}

export function roomSummary(row) {
  const count = Array.isArray(row?.room_items) ? row.room_items.length : 0
  if (count > 1) return `${count} 间 · ${row.room_no || '--'}`
  return row?.room_no || '--'
}

export function formatImportIssue(issue) {
  if (!issue) return ''
  if (typeof issue === 'string') return issue
  const prefix = [
    issue.row ? `第${issue.row}行` : '',
    issue.contract_no ? `合同 ${issue.contract_no}` : ''
  ].filter(Boolean).join(' ')
  return `${prefix ? `${prefix}: ` : ''}${issue.reason || issue.message || '导入失败'}`
}

export function normalizeImportIssues(rows, fallbackRows = []) {
  const formatted = [
    ...(Array.isArray(rows) ? rows.map(formatImportIssue) : []),
    ...(Array.isArray(fallbackRows) ? fallbackRows.map(formatImportIssue) : [])
  ].filter(Boolean)
  return [...new Set(formatted)]
}

export function buildImportSuccessResult(result) {
  const issues = normalizeImportIssues(result.skipped_rows, result.errors)
  return {
    type: result.skipped > 0 ? 'warning' : 'success',
    title: `导入完成：成功 ${result.imported || 0} 条，跳过 ${result.skipped || 0} 条`,
    issues: issues.slice(0, 5),
    extraCount: Math.max(0, issues.length - 5)
  }
}

export function buildImportErrorResult(error) {
  const detail = error.payload?.detail
  if (detail && typeof detail === 'object') {
    const issues = normalizeImportIssues(detail.fatal_errors, detail.errors)
    return {
      type: 'error',
      title: detail.message || '导入失败，未写入新合同',
      issues: issues.slice(0, 5),
      extraCount: Math.max(0, issues.length - 5)
    }
  }
  return {
    type: 'error',
    title: error.message || '导入失败，未写入新合同',
    issues: [],
    extraCount: 0
  }
}
