import { locale } from '../i18n/index.js'
export function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) return '0'
  return Number(value).toLocaleString(locale.value === 'en' ? 'en-GB' : 'zh-CN')
}

export function formatArea(value) {
  if (value == null || Number.isNaN(Number(value))) return '0.00'
  return Number(value).toLocaleString(locale.value === 'en' ? 'en-GB' : 'zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatCurrency(value) {
  if (value == null || Number.isNaN(Number(value))) return '0.00'
  return Number(value).toLocaleString(locale.value === 'en' ? 'en-GB' : 'zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
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
  return date.toLocaleString(locale.value === 'en' ? 'en-GB' : 'zh-CN', {
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
    case '已变更':
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
      return '查看'
    default:
      return role || '--'
  }
}

export function formatAuditOperator(operator) {
  switch (operator) {
    case 'system':
      return '系统任务'
    case 'seed_demo_data':
      return '模拟数据脚本'
    default:
      return operator || '--'
  }
}

export function formatAuditAction(action) {
  const execution = { execution_create: '建立处理事项', execution_update: '更新处理事项', execution_verify: '核验合同动作', execution_benefit_verify: '登记收益核验' }
  if (execution[action]) return execution[action]
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
    case 'terminate':
      return '退租'
    case 'change_confirm':
      return '变更签确认'
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
    case 'receivable_update':
      return '更新应收状态'
    case 'receivable_payment':
      return '登记收款核销'
    case 'receivable_payment_delete':
      return '删除收款核销'
    case 'document_generate':
      return '生成业务文档'
    case 'room_expand':
      return '扩租'
    case 'room_reduce':
      return '减租'
    case 'room_swap':
      return '换房'
    case 'rent_term_create':
      return '新增租金条款'
    case 'rent_term_update':
      return '修改租金条款'
    case 'rent_term_delete':
      return '删除租金条款'
    case 'customer_create':
      return '新增客户'
    case 'customer_update':
      return '修改客户'
    case 'customer_profile_create':
      return '建立楼宇客户资料'
    case 'customer_profile_update':
      return '修改楼宇客户资料'
    case 'customer_followup_create':
      return '新增客户跟进'
    case 'customer_followup_update':
      return '修改客户跟进'
    case 'customer_followup_delete':
      return '删除客户跟进'
    case 'customer_followup_done':
      return '完成客户跟进'
    case 'customer_import':
      return '导入客户'
    case 'customer_profile_import':
      return '导入楼宇客户资料'
    case 'customer_master_import':
      return '导入共享客户主档'
    case 'followup_import':
      return '导入客户跟进'
    case 'deposit_create':
      return '登记应收押金'
    case 'deposit_update':
      return '修改押金'
    case 'deposit_receive':
      return '登记押金收取'
    case 'deposit_refund':
      return '登记押金退还'
    case 'deposit_offset':
      return '登记押金冲抵'
    case 'attachment_upload':
      return '上传附件'
    case 'attachment_update':
      return '编辑附件'
    case 'attachment_delete':
      return '删除附件'
    case 'ai_customer_summary':
      return 'AI 客户摘要'
    case 'ai_follow_suggestion':
      return 'AI 跟进建议'
    case 'ai_risk_explain':
      return 'AI 风险解释'
    case 'config_update':
      return '修改系统配置'
    case 'client_error':
      return '前端错误'
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
      return '租金需复核'
    case 'negative_values':
      return '负数需复核'
    case 'missing_room_reference':
      return '房间缺失'
    default:
      return type || '--'
  }
}
