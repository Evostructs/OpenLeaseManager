import { locale, setCurrencyUnit } from '../i18n/index.js'
import { beginReadyTask, operationNow, recordApiTiming } from './operationTelemetry.js'
import { buildScopedApiUrl, buildScopedDownloadUrl } from './propertyScope.js'

export const AUTH_EXPIRED_EVENT = 'auth-expired'
export const AUTH_EXPIRED_MESSAGE = '登录已失效，请重新登录'
const AUTH_EXPIRED_STORAGE_KEY = 'demo_a-auth-expired'

let lastAuthExpiredAt = 0

function emitAuthExpired(message) {
  const now = Date.now()
  if (now - lastAuthExpiredAt < 1000) return
  lastAuthExpiredAt = now
  const normalizedMessage = message || AUTH_EXPIRED_MESSAGE
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: { message: normalizedMessage } }))
  try {
    window.localStorage.setItem(
      AUTH_EXPIRED_STORAGE_KEY,
      JSON.stringify({ at: now, message: normalizedMessage })
    )
  } catch {
    // ignore storage failures; same-tab redirect still works
  }
}

const FIELD_LABELS = {
  occurred_on: '实际日期',
  counterparty: '往来方',
  import_key: '银行流水编号',
  service_from: '服务开始月',
  service_to: '服务结束月',
  supplier: '供应商',
  cost_category: '费用类别',
  evidence: '凭据依据',
  reason: '操作原因',
  contract_no: '合同编号',
  tenant_name: '租户名称',
  tower: '楼座',
  room_no: '室号',
  area: '面积',
  unit_price: '单价',
  monthly_rent: '月租金',
  free_period: '免租期',
  broker_fee: '佣金',
  deposit: '押金',
  deposit_amount: '押金金额',
  property_fee_rate: '物业费单价',
  property_fee_free: '物业费减免',
  start_date: '起租日',
  end_date: '终止日',
  payment_date: '收款日期',
  amount: '金额',
  monthly_amount: '月金额',
  adjustment_value: '调整值',
  contact_person: '联系人',
  contact_phone: '联系电话',
  contact_email: '联系邮箱',
  customer_id: '客户',
  room_items: '房源明细',
  username: '用户名',
  password: '密码',
  month: '月份',
  notes: '备注'
}

const VALIDATION_REASONS = [
  ['missing', '为必填项'],
  ['float_parsing', '应为数字'],
  ['int_parsing', '应为整数'],
  ['int_from_float', '应为整数'],
  ['date_', '应为有效日期（YYYY-MM-DD）'],
  ['string_too_long', '长度超出限制'],
  ['string_too_short', '长度不足'],
  ['greater_than', '数值过小'],
  ['less_than', '数值过大'],
  ['bool_', '应为是/否'],
  ['enum', '取值无效'],
  ['json_invalid', '格式不正确']
]

function describeValidationReason(type, msg) {
  const normalized = String(type || '')
  // 后端自定义校验器（field_validator）抛出的中文信息直接展示
  if (normalized === 'value_error' && msg) {
    return `：${String(msg).replace(/^Value error,\s*/i, '')}`
  }
  for (const [prefix, text] of VALIDATION_REASONS) {
    if (normalized.startsWith(prefix)) return text
  }
  return msg ? `格式不正确（${msg}）` : '格式不正确'
}

// Pydantic 422 的 detail 是对象数组，直接塞进 Error 会显示成 [object Object]，
// 这里转成用户可读的中文字段提示。
function formatValidationDetail(detail) {
  if (!Array.isArray(detail) || !detail.length) return null
  const parts = detail.slice(0, 3).map((item) => {
    const segments = Array.isArray(item?.loc)
      ? item.loc.filter((seg) => seg !== 'body' && seg !== 'query' && typeof seg !== 'number')
      : []
    const field = segments.length ? segments[segments.length - 1] : ''
    const label = FIELD_LABELS[field] || field
    const reason = describeValidationReason(item?.type, item?.msg)
    return label ? `${label}${reason}` : reason
  })
  const suffix = detail.length > 3 ? ` 等 ${detail.length} 项` : ''
  return `填写有误：${parts.join('；')}${suffix}`
}

function extractErrorMessage(payload) {
  if (typeof payload === 'string') return payload
  const detail = payload?.detail
  if (typeof detail === 'string' && detail) return detail
  const validationMessage = formatValidationDetail(detail)
  if (validationMessage) return validationMessage
  if (typeof payload?.message === 'string' && payload.message) return payload.message
  return '请求失败'
}

export async function api(path, options = {}) {
  const { skipAuthRedirect = false, includeScope = true, responseType = 'json', ...requestOptions } = options
  const config = {
    headers: {
      ...(requestOptions.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(requestOptions.headers || {})
    },
    credentials: 'include',
    ...requestOptions
  }

  config.headers = { ...config.headers, 'X-OpenLease-Locale': locale.value }
  const started = operationNow()
  const finishReady = beginReadyTask('api')
  let headerTime = null, responseStatus = 0, serverMs = null
  try {
    const response = includeScope
      ? await fetch(buildScopedApiUrl(path), config)
      : await fetch(buildScopedApiUrl(path, { includeScope: false }), config)
    setCurrencyUnit(response.headers.get('x-openlease-currency'))
    headerTime = operationNow()
    responseStatus = response.status
    const metric = (response.headers.get('server-timing') || '').match(/(?:^|,)\s*app\s*;\s*dur=([\d.]+)/)
    serverMs = metric ? Number(metric[1]) : null
    if (response.status === 204) return null
    if (response.ok && responseType === 'blob') return await response.blob()

    const contentType = response.headers.get('content-type') || ''
    const payload = contentType.includes('application/json') ? await response.json() : await response.text()

    if (!response.ok) {
      const message = extractErrorMessage(payload)
      if (response.status === 401 && !skipAuthRedirect) {
        emitAuthExpired(AUTH_EXPIRED_MESSAGE)
      }
      const error = new Error(message)
      error.status = response.status
      error.payload = payload
      throw error
    }

    return payload
  } finally {
    const ended = operationNow()
    recordApiTiming({ endpoint: path, start: started, duration: ended - started, headersMs: headerTime === null ? null : headerTime - started, bodyMs: headerTime === null ? null : ended - headerTime, serverMs, status: responseStatus })
    finishReady()
  }
}

export function openDownload(url, options = {}) {
  window.open(buildScopedDownloadUrl(url, options), '_blank', 'noopener,noreferrer')
}

export function readAuthExpiredBroadcast(event) {
  if (event.key !== AUTH_EXPIRED_STORAGE_KEY || !event.newValue) return null
  try {
    const payload = JSON.parse(event.newValue)
    return payload?.message || AUTH_EXPIRED_MESSAGE
  } catch {
    return AUTH_EXPIRED_MESSAGE
  }
}
