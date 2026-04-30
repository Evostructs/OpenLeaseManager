export function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) return '0'
  return Number(value).toLocaleString('zh-CN')
}

export function formatArea(value) {
  if (value == null || Number.isNaN(Number(value))) return '0.00'
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatCurrency(value) {
  if (value == null || Number.isNaN(Number(value))) return '0.00'
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatCurrencyFixed(value) {
  return formatCurrency(value)
}

export function formatDate(value) {
  if (!value) return '--'
  return String(value).slice(0, 10)
}

export function formatDateTime(value) {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return String(value).replace('T', ' ').slice(0, 19)
  }
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  })
}

export function statusTone(status) {
  switch (status) {
    case '在租':
    case '已续租':
      return 'success'
    case '即将到期':
    case '逾期占用':
      return 'warning'
    case '已到期':
    case '已退租':
      return 'error'
    default:
      return 'info'
  }
}

export function formatRole(role) {
  switch (role) {
    case 'admin':
      return '管理员'
    case 'editor':
      return '编辑'
    case 'viewer':
      return '只读'
    default:
      return role || '--'
  }
}

export function formatAuditOperator(operator) {
  switch (operator) {
    case 'system':
      return '系统任务'
    default:
      return operator || '--'
  }
}

export function formatAuditAction(action) {
  switch (action) {
    case 'create':
      return '创建合同'
    case 'update':
      return '修改合同'
    case 'delete':
      return '删除合同'
    case 'import':
      return '导入合同'
    case 'restore':
      return '恢复合同'
    case 'room_create':
      return '新增房间'
    case 'room_update':
      return '修改房间'
    case 'room_delete':
      return '删除房间'
    case 'room_restore':
      return '恢复房间'
    case 'room_clear':
      return '清空房间回收站'
    default:
      return action || '--'
  }
}

export function formatIssueType(type) {
  switch (type) {
    case 'missing_contract_pdfs':
      return '合同缺少 PDF'
    case 'orphan_pdfs':
      return '孤立 PDF'
    case 'missing_dates':
      return '日期缺失'
    case 'invalid_dates':
      return '日期错误'
    case 'duplicate_room':
      return '重复房间'
    case 'zero_rent':
      return '租金异常'
    case 'negative_values':
      return '负数异常'
    case 'missing_room_reference':
      return '房间缺失'
    default:
      return type || '--'
  }
}
