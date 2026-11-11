<script setup>
import { startOperation } from '../lib/operationTelemetry'
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import EChartPanel from './EChartPanel.vue'
import ExecutionActions from './ExecutionActions.vue'
import FinancialCoverage from './FinancialCoverage.vue'
import ForecastStrategyLink from './ForecastStrategyLink.vue'
import BaseModal from './BaseModal.vue'
import { planningDrafts, planningIdentity } from '../lib/planningDrafts.js'
import { createPlanningLeaveGuard, confirmPlanningLeaveWithoutEditor } from '../lib/planningLeaveGuard.js'
import {
  calculateBudgetScenario,
  compareAssetStrategies,
  createBlankAssetStrategy,
  createDemoAssetStrategy
} from '../lib/decisionScenarioDomain'
import '../styles/decision-workbench.css'
import { createFinancialPlanningApi } from '../lib/financialPlanningApi.js'
import {
  applyFinancialForecast, buildSourcePlanningBase, buildSourcePlanningBridge, createPlanningScenario, currentPlanningInput,
  expenseDraft, expenseHasChanges, expenseRequest, financialForecastChanged, planningBridgeBars, planningDrillRows, versionBudgetReference
} from '../lib/financialPlanningDomain.js'
import { watchPlanningDraftChanges } from '../lib/planningDraftWatch.js'

const props = defineProps({
  active: { type: Boolean, default: true },
  scopeKey: { type: String, default: 'portfolio' },
  scopeLabel: { type: String, default: '项目组合' },
  user: { type: Object, default: () => ({ role: 'viewer' }) },
  buildingCode: { type: String, default: '' },
  canReadPlanning: { type: Boolean, default: false },
  canWritePlanning: { type: Boolean, default: false }
})
const emit = defineEmits(['open-contract'])

const clone = (value) => JSON.parse(JSON.stringify(value))
const yearNow = new Date().getFullYear()
const months = Array.from({ length: 12 }, (_, index) => index + 1)
const activeTab = ref('budget')
const chartMetric = ref('ebitda')
const tableMode = ref('budget')
const message = ref('')
const errorMessage = ref('')
const validationRequested = ref(false)
const planName = ref('')
const selectedYear = ref(yearNow)
const selectedCutoff = ref(new Date().getMonth())
const loading = ref(false)
const saving = ref(false)
const serverCanWrite = ref(false)
const serverRevision = ref(0)
const versions = shallowRef([])
const selectedVersionId = ref(null)
const baseVersionId = ref(null)
const activeForecastId = ref(null)
const approvedVersion = shallowRef(null)
const activeBudgetVersion = shallowRef(null)
const sourceForecast = shallowRef(null)
const snapshotVersionId = ref(null)
const comparison = shallowRef(null)
const expenses = ref([])
const contractRows = ref([])
const contractDetails = ref(null)
const expenseDetails = ref(null)
const bridgeSelection = ref('contract')
const drillMonth = ref(0)
const savedAt = ref('')
const dirty = ref(false)
const comparisonIds = ref([])
let contextEpoch = 0
let operationSequence = 0
let suppressDirty = false
let workspacePending = false
let workspaceForecastReference = null
const readable = computed(() => Boolean(props.canReadPlanning && props.buildingCode))
const writable = computed(() => readable.value && props.canWritePlanning && serverCanWrite.value)
const client = computed(() => readable.value ? createFinancialPlanningApi(props.buildingCode) : null)
const selectedVersion = computed(() => versions.value.find(item => item.id === selectedVersionId.value))
const hasExpenseChanges = computed(() => expenses.value.some(expenseHasChanges))
const adjustments = computed(() => contractRows.value
  .filter(row => Number(row.start_shift_months) !== 0 || Number(row.rent_change_percent) !== 0)
  .map(row => ({ contract_id: row.contract_id, start_shift_months: Number(row.start_shift_months), rent_change_percent: Number(row.rent_change_percent) })))

function blankState() {
  return {
    scenario: createPlanningScenario(selectedYear.value, selectedCutoff.value), approved: null, editingBudget: true,
    started: true, saved: [], strategy: createBlankAssetStrategy(),
    strategyStarted: true,
    leaseOwner: '', savingOwner: ''
  }
}
const state = ref(blankState())
const editRevision = ref(0)
const parkedRevision = ref(-1)
const draftEditor = Symbol('planning-editor')
const draftOwner = computed(() => planningIdentity(props.user))
const draftContext = computed(() => JSON.stringify([draftOwner.value, props.buildingCode, selectedYear.value]))
const hasUnparkedChanges = computed(() => (dirty.value || hasExpenseChanges.value) && editRevision.value !== parkedRevision.value)
const leaveGuard = createPlanningLeaveGuard({
  changed: () => hasUnparkedChanges.value,
  busy: () => saving.value || loading.value,
  save: () => parkDraft(),
  discard: async () => { removeStoredDraft(); await loadWorkspace({ restoreDraft: false }) }
})
const leavePrompt = leaveGuard.state
function draftChanged() {
  if (!suppressDirty && !loading.value && !saving.value) editRevision.value++
}
watch(expenses, draftChanged, { deep: true, flush: 'sync' })
watch(selectedCutoff, draftChanged, { flush: 'sync' })
watch(() => [dirty.value || hasExpenseChanges.value, saving.value, readable.value], () => {
  planningDrafts.pending(draftEditor, readable.value && (dirty.value || hasExpenseChanges.value || saving.value))
}, { flush: 'sync' })
watch(draftOwner, owner => { planningDrafts.identify(owner); leaveGuard.cancel() }, { immediate: true, flush: 'sync' })
watch(() => [props.buildingCode, props.canReadPlanning], () => leaveGuard.cancel(), { flush: 'sync' })

function captureDraft() {
  return clone({ state: state.value, planName: planName.value, selectedCutoff: selectedCutoff.value,
    approvedVersion: approvedVersion.value, baseVersionId: baseVersionId.value,
    sourceForecast: sourceForecast.value, snapshotVersionId: snapshotVersionId.value,
    selectedVersionId: selectedVersionId.value, serverRevision: serverRevision.value,
    contractRows: contractRows.value, expenses: expenses.value, comparison: comparison.value,
    savedAt: savedAt.value, dirty: dirty.value, activeTab: activeTab.value })
}
function parkDraft() {
  if (!readable.value || loading.value || saving.value) throw new Error('当前正在处理数据，请稍后暂存。')
  planningDrafts.save(draftOwner.value, props.buildingCode, selectedYear.value, captureDraft())
  parkedRevision.value = editRevision.value
  message.value = '草稿已暂存，返回本楼宇、本年度会恢复。仅本次页面有效；未采用预测、未冻结预算、未保存费用。'
}
function parkDraftFromButton() {
  try { parkDraft() } catch (error) { showError(error) }
}
function removeStoredDraft() {
  planningDrafts.remove(draftOwner.value, props.buildingCode, selectedYear.value)
  parkedRevision.value = -1
}
function restoreStoredDraft() {
  const draft = planningDrafts.get(draftOwner.value, props.buildingCode, selectedYear.value)
  if (!draft || !readable.value) return false
  suppressDirty = true
  try {
    state.value = draft.state
    planName.value = draft.planName
    selectedCutoff.value = draft.selectedCutoff
    approvedVersion.value = draft.approvedVersion
    baseVersionId.value = draft.baseVersionId
    sourceForecast.value = draft.sourceForecast
    snapshotVersionId.value = draft.snapshotVersionId
    selectedVersionId.value = draft.selectedVersionId
    serverRevision.value = draft.serverRevision
    contractRows.value = draft.contractRows
    expenses.value = draft.expenses
    comparison.value = draft.comparison
    savedAt.value = draft.savedAt
    dirty.value = draft.dirty
    activeTab.value = draft.activeTab
    parkedRevision.value = editRevision.value
    message.value = '已恢复暂存草稿。业务基准可能已更新，保存正式版本前请刷新业务数据并核对；费用修改仍需单独保存。'
  } finally { suppressDirty = false }
  return true
}
async function requestLeave(label = '离开当前财务工作区', options = {}) {
  if (!readable.value) return confirmPlanningLeaveWithoutEditor(options)
  if (saving.value || loading.value) {
    message.value = '正在处理数据，请完成后再切换。'
    return false
  }
  const context = draftContext.value
  const allowed = await leaveGuard.request(label, options)
  return allowed && readable.value && context === draftContext.value
}
async function changeYear(event) {
  const year = Number(event.target.value)
  event.target.value = String(selectedYear.value)
  if (!Number.isInteger(year) || year < 2000 || year > 2100 || year === selectedYear.value) return
  if (!await requestLeave(`切换到 ${year} 年`)) return
  selectedYear.value = year
  await loadWorkspace()
}
function selectHistory(event) {
  const id = Number(event.target.value)
  event.target.value = selectedVersionId.value || ''
  void restoreVersion(id)
}
defineExpose({ requestLeave })
watch(() => [props.user?.id, props.user?.username, props.user?.role,
  props.user?.scope?.authorization_version ?? props.user?.authorization_version,
  props.user?.scope?.gate_policy_version, props.buildingCode, props.canReadPlanning].join('|'), () => {
  void loadWorkspace()
}, { immediate: true })
watch(() => props.active, active => { if (active && workspacePending) void loadWorkspace() })
watchPlanningDraftChanges({ state, adjustments, planName }, () => {
  if (!suppressDirty && !loading.value) { dirty.value = true; draftChanged() }
})
onBeforeUnmount(() => { contextEpoch += 1; operationSequence += 1; leaveGuard.cancel(); planningDrafts.release(draftEditor) })

const scenario = computed(() => state.value.scenario)
const needsMeasureConfirmation = computed(() => (scenario.value.newLease.enabled || scenario.value.costSaving.enabled) && !scenario.value.baselineExcludesMeasures)
const currentInput = computed(() => currentPlanningInput(scenario.value, state.value.approved))
const result = computed(() => calculateBudgetScenario(currentInput.value))
const ready = computed(() => Boolean(state.value.approved && result.value.valid))
const annual = computed(() => ready.value ? result.value.annual : null)
const basisResult = computed(() => calculateBudgetScenario({
  ...currentInput.value,
  newLease: { enabled: false }, costSaving: { enabled: false }
}))
const draftResult = computed(() => calculateBudgetScenario({
  ...scenario.value, newLease: { enabled: false }, costSaving: { enabled: false }
}))
const draftBudgetTotal = computed(() => {
  const values = scenario.value.budget.map(rowEbitda)
  return values.every((value) => value !== null) ? values.reduce((total, value) => total + value, 0) : null
})
const draftBudgetGap = computed(() => scenario.value.annualTarget !== null && scenario.value.annualTarget !== '' && draftBudgetTotal.value !== null
  ? Number(scenario.value.annualTarget) - draftBudgetTotal.value : null)
const budgetToDate = computed(() => basisResult.value.valid
  ? basisResult.value.months.filter((row) => row.closed).reduce((total, row) => total + row.budgetEbitda, 0)
  : null)
const actualVariance = computed(() => basisResult.value.valid
  ? basisResult.value.annual.actualToDate - budgetToDate.value : null)
const availableStartMonths = computed(() => Array.from(
  { length: 24 - Number(currentInput.value.closedThroughMonth || 0) },
  (_, index) => Number(currentInput.value.closedThroughMonth || 0) + index + 1
))
const maximumMonthlySaving = computed(() => {
  const costs = currentInput.value.forecast
    .filter((row, index) => index + 1 >= Number(scenario.value.costSaving.startMonth) && index + 1 > Number(currentInput.value.closedThroughMonth))
    .map((row) => row.operatingCost)
    .filter((value) => value !== null && value !== '' && Number.isFinite(Number(value)))
    .map(Number)
  return costs.length ? Math.max(0, Math.min(...costs)) : 100
})
const strategyResult = computed(() => compareAssetStrategies(state.value.strategy))
const strategyReady = computed(() => strategyResult.value.valid && strategyResult.value.applicable)
const strategyFields = {
  hold: [
    ['annualNetCashFlow', '首年经营净现金流', '万元', -1000000, 1000000, 1],
    ['annualGrowthRate', '经营净现金流年增长率', '%', -99, 100, 0.5],
    ['exitPrice', '期末预计售价', '万元', 0, 100000000, 10],
    ['exitCostRate', '期末处置费用率', '%', 0, 100, 0.1]
  ],
  renovate: [
    ['capex', '期初改造投入', '万元', 0, 100000000, 10],
    ['annualNetCashFlow', '改造后首年经营净现金流', '万元', -1000000, 1000000, 1],
    ['annualGrowthRate', '经营净现金流年增长率', '%', -99, 100, 0.5],
    ['firstYearRentLoss', '首年停租损失（额外扣除）', '万元', 0, 100000000, 1],
    ['exitPrice', '期末预计售价', '万元', 0, 100000000, 10],
    ['exitCostRate', '期末处置费用率', '%', 0, 100, 0.1]
  ],
  sell: [
    ['price', '当前预计出售价格', '万元', 0, 100000000, 10],
    ['transactionCost', '本次交易费用合计', '万元', 0, 100000000, 1]
  ]
}

function numberText(value, digits = 1) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—'
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}
function signed(value) {
  return Number.isFinite(value) ? `${value > 0 ? '+' : ''}${numberText(value)}` : '—'
}
function gapText(value) {
  return value === null || value === undefined ? '等待填写' : value > 0 ? `仍差 ${numberText(value)}` : value < 0 ? `超出 ${numberText(-value)}` : '恰好达标'
}
function monthLabel(value) {
  return Number(value) <= 12 ? `${value}月` : `次年${Number(value) - 12}月`
}
function latestRow(index) {
  return index < Number(scenario.value.closedThroughMonth) ? scenario.value.actual[index] : scenario.value.forecast[index]
}
function rowEbitda(row) {
  if (row?.revenue === null || row?.revenue === '' || row?.operatingCost === null || row?.operatingCost === '') return null
  const revenue = Number(row?.revenue)
  const cost = Number(row?.operatingCost)
  return Number.isFinite(revenue) && Number.isFinite(cost) ? revenue - cost : null
}
function showError(error) {
  errorMessage.value = error?.status === 409
    ? '数据已由其他人更新。请点击“刷新业务数据”读取最新版本后再保存。'
    : error?.message || '加载失败，请稍后重试。'
}
function previewPayload() {
  return { year: Number(selectedYear.value), closed_through_month: Number(selectedCutoff.value), base_version_id: baseVersionId.value, contract_adjustments: clone(adjustments.value) }
}
function applyApproved(version) {
  approvedVersion.value = version || null
  if (!version) { state.value.approved = null; return }
  const baseline = applyFinancialForecast(createPlanningScenario(version.year), version.snapshot, { seedBudget: true })
  baseline.annualTarget = version.client_state?.scenario?.annualTarget ?? baseline.annualTarget
  state.value.approved = { year: version.year, budget: clone(baseline.budget), annualTarget: baseline.annualTarget }
  state.value.scenario.budget = clone(baseline.budget)
  state.value.scenario.annualTarget = baseline.annualTarget
}
function setContractRows(forecast, savedAdjustments = adjustments.value) {
  const changes = new Map(savedAdjustments.map(item => [Number(item.contract_id), item]))
  contractRows.value = (forecast.sources || []).filter(item => item.source_type === 'contract').map(item => ({
    ...item, contract_id: item.contract_id || item.source_id,
    start_shift_months: changes.get(Number(item.contract_id || item.source_id))?.start_shift_months ?? 0,
    rent_change_percent: changes.get(Number(item.contract_id || item.source_id))?.rent_change_percent ?? 0
  }))
}
function useForecast(forecast, seedBudget = false, versionId = null) {
  const baselineChanged = financialForecastChanged(sourceForecast.value || workspaceForecastReference, forecast)
  if (baselineChanged && (scenario.value.newLease.enabled || scenario.value.costSaving.enabled)) {
    scenario.value.baselineExcludesMeasures = false
    message.value = '业务基准已变化，请核对新增租约和降本是否已计入。'
  }
  snapshotVersionId.value = versionId
  sourceForecast.value = forecast
  state.value.scenario = applyFinancialForecast(scenario.value, forecast, { seedBudget })
  selectedCutoff.value = forecast.closed_through_month
  setContractRows(forecast)
  workspaceForecastReference = null
}
function hydrateVersion(version) {
  const payload = version.client_state || {}
  const preservedBudget = state.value.approved
  const nextScenario = { ...createPlanningScenario(version.year, version.closed_through_month), ...clone(payload.scenario || {}) }
  state.value.scenario = applyFinancialForecast(nextScenario, version.snapshot, { seedBudget: !payload.scenario?.budget })
  if (preservedBudget) {
    state.value.scenario.budget = clone(preservedBudget.budget)
    state.value.scenario.annualTarget = preservedBudget.annualTarget
  }
  state.value.strategy = clone(payload.strategy || createBlankAssetStrategy())
  state.value.leaseOwner = payload.leaseOwner || ''
  state.value.savingOwner = payload.savingOwner || ''
  state.value.editingBudget = !preservedBudget
  sourceForecast.value = version.snapshot
  snapshotVersionId.value = version.id
  selectedCutoff.value = version.closed_through_month
  selectedVersionId.value = version.id
  baseVersionId.value = version.id
  setContractRows(version.snapshot, payload.contractAdjustments || [])
  planName.value = version.name || ''
  savedAt.value = version.created_at || ''
  workspaceForecastReference = null
}
async function alignBudgetToVersion(version, epoch, sequence) {
  const id = versionBudgetReference(version, activeBudgetVersion.value)
  if (!id) {
    const saved = version.client_state?.scenario
    if (saved?.budget?.length === 12) state.value.approved = { year: version.year, budget: clone(saved.budget), annualTarget: saved.annualTarget }
    else state.value.approved = null
    approvedVersion.value = null
    comparison.value = null
    return true
  }
  const budget = activeBudgetVersion.value?.id === id ? activeBudgetVersion.value
    : approvedVersion.value?.id === id ? approvedVersion.value : await client.value.version(id)
  if (epoch !== contextEpoch || sequence !== operationSequence) return false
  if (budget.kind !== 'budget' || budget.year !== version.year) throw new Error('该方案关联的预算版本无效，请刷新后重新选择。')
  applyApproved(budget)
  return true
}
function restoreClientControls(payload = {}) {
  if (payload.scenario?.newLease) scenario.value.newLease = clone(payload.scenario.newLease)
  if (payload.scenario?.costSaving) scenario.value.costSaving = clone(payload.scenario.costSaving)
  scenario.value.baselineExcludesMeasures = payload.scenario?.baselineExcludesMeasures === true
  state.value.strategy = clone(payload.strategy || createBlankAssetStrategy())
  state.value.leaseOwner = payload.leaseOwner || ''
  state.value.savingOwner = payload.savingOwner || ''
}
async function updateComparison(epoch = contextEpoch, sequence = operationSequence) {
  if (!client.value || !approvedVersion.value) { comparison.value = null; return }
  const currentVersionId = snapshotVersionId.value
  comparison.value = null
  const result = await client.value.compare({ ...previewPayload(), baseline_version_id: approvedVersion.value.id, ...(currentVersionId ? { current_version_id: currentVersionId } : {}) }, { includeBaseline: false })
  if (epoch !== contextEpoch || sequence !== operationSequence) return
  comparison.value = result.comparison
  useForecast(result.current, false, currentVersionId)
}
async function updateLiveForecast(epoch, sequence) {
  if (epoch !== contextEpoch || sequence !== operationSequence) return false
  const api = client.value
  const payload = previewPayload()
  if (!approvedVersion.value) {
    const forecast = await api.preview(payload)
    if (epoch !== contextEpoch || sequence !== operationSequence) return false
    useForecast(forecast)
    comparison.value = null
    return true
  }
  let result
  try {
    result = await api.compare({ ...payload, baseline_version_id: approvedVersion.value.id }, { includeBaseline: false })
  } catch (error) {
    if (epoch !== contextEpoch || sequence !== operationSequence) return false
    // A failed comparison must still allow a live forecast refresh, as before.
    const forecast = await api.preview(payload)
    if (epoch !== contextEpoch || sequence !== operationSequence) return false
    useForecast(forecast)
    comparison.value = null
    throw error
  }
  if (epoch !== contextEpoch || sequence !== operationSequence) return false
  useForecast(result.current)
  comparison.value = result.comparison
  return true
}
async function loadWorkspace(options = {}) {
  workspaceForecastReference = null
  if (!props.active && readable.value) {
    ++contextEpoch; ++operationSequence
    workspacePending = true
    loading.value = false; saving.value = false
    return
  }
  workspacePending = false
  const epoch = ++contextEpoch
  const sequence = ++operationSequence
  suppressDirty = true
  state.value = blankState()
  sourceForecast.value = null
  snapshotVersionId.value = null
  comparison.value = null
  versions.value = []
  expenses.value = []
  contractRows.value = []
  approvedVersion.value = null
  activeBudgetVersion.value = null
  selectedVersionId.value = null
  baseVersionId.value = null
  activeForecastId.value = null
  comparisonIds.value = []
  planName.value = ''
  savedAt.value = ''
  dirty.value = false
  message.value = ''
  errorMessage.value = ''
  validationRequested.value = false
  serverCanWrite.value = false
  saving.value = false
  activeTab.value = 'budget'
  suppressDirty = false
  if (!readable.value) { loading.value = false; return }
  loading.value = true
  try {
    const response = await client.value.source(Number(selectedYear.value), Number(selectedCutoff.value), { forecastMode: 'workspace_deferred' })
    if (epoch !== contextEpoch || sequence !== operationSequence) return
    serverRevision.value = response.revision
    serverCanWrite.value = response.can_write === true
    versions.value = response.versions || []
    expenses.value = (response.expenses || []).map(item => expenseDraft(item, selectedYear.value))
    activeBudgetVersion.value = response.approved_budget
    applyApproved(response.approved_budget)
    activeForecastId.value = response.latest_forecast?.id || null
    if (response.latest_forecast) {
      if (!await alignBudgetToVersion(response.latest_forecast, epoch, sequence)) return
      hydrateVersion(response.latest_forecast)
    }
    else {
      if (response.approved_budget) {
        restoreClientControls(response.approved_budget.client_state)
        workspaceForecastReference = response.approved_budget.snapshot
      }
      if (response.forecast || !response.approved_budget) useForecast(response.forecast, !state.value.approved)
      if (response.approved_budget) savedAt.value = response.approved_budget.created_at
    }
    state.value.editingBudget = !state.value.approved
    activeTab.value = state.value.approved ? 'measures' : 'budget'
    if (!response.latest_forecast && !response.forecast && response.approved_budget) {
      if (!await updateLiveForecast(epoch, sequence)) return
    } else await updateComparison(epoch, sequence)
    if (epoch !== contextEpoch || sequence !== operationSequence) return
    dirty.value = false
    parkedRevision.value = -1
    if (options.restoreDraft !== false) restoreStoredDraft()
    return true
  } catch (error) { if (epoch === contextEpoch && sequence === operationSequence) showError(error); return false }
  finally { if (epoch === contextEpoch && sequence === operationSequence) loading.value = false }
}
async function refreshData(options = {}) {
  if (!readable.value || loading.value || saving.value) return
  const epoch = contextEpoch
  const sequence = ++operationSequence
  loading.value = true
  errorMessage.value = ''
  const expenseChanges = options.preserveExpenseDrafts !== false ? expenses.value.filter(expenseHasChanges).map(clone) : []
  try {
    const response = await client.value.source(Number(selectedYear.value), Number(selectedCutoff.value), { forecastMode: 'none', includeLatestSnapshot: false })
    if (epoch !== contextEpoch || sequence !== operationSequence) return
    serverRevision.value = response.revision
    serverCanWrite.value = response.can_write === true
    versions.value = response.versions || []
    expenses.value = (response.expenses || []).map(item => expenseDraft(item, selectedYear.value))
    if (expenseChanges.length) {
      const changed = new Map(expenseChanges.filter(item => item.id).map(item => [item.id, item]))
      expenses.value = [...expenses.value.map(item => changed.get(item.id) || item), ...expenseChanges.filter(item => !item.id)]
    }
    activeBudgetVersion.value = response.approved_budget
    activeForecastId.value = response.latest_forecast?.id || null
    if (!await updateLiveForecast(epoch, sequence)) return
    if (epoch !== contextEpoch || sequence !== operationSequence) return
    dirty.value = true
    editRevision.value++
    message.value = needsMeasureConfirmation.value ? '业务基准已变化，请核对新增租约和降本是否已计入。' : '合同和费用预测已更新。保存并采用后，下次会载入这份预测。'
    return true
  } catch (error) { if (epoch === contextEpoch && sequence === operationSequence) showError(error); return false }
  finally { if (epoch === contextEpoch && sequence === operationSequence) loading.value = false }
}
async function calculateContractChanges() {
  if (!readable.value || loading.value || saving.value) return
  const epoch = contextEpoch
  const sequence = ++operationSequence
  loading.value = true
  errorMessage.value = ''
  try {
    if (!await updateLiveForecast(epoch, sequence)) return
    if (epoch !== contextEpoch || sequence !== operationSequence) return
    dirty.value = true
    editRevision.value++
    message.value = needsMeasureConfirmation.value ? '业务基准已变化，请核对新增租约和降本是否已计入。' : '已按当前起租和租金假设重新计算。'
  } catch (error) { if (epoch === contextEpoch && sequence === operationSequence) showError(error) }
  finally { if (epoch === contextEpoch && sequence === operationSequence) loading.value = false }
}
async function useActiveBudget() {
  if (!readable.value || !activeBudgetVersion.value || loading.value || saving.value) return
  if (!await requestLeave('使用当前批准预算')) return
  const epoch = contextEpoch
  const sequence = ++operationSequence
  loading.value = true
  errorMessage.value = ''
  try {
    applyApproved(activeBudgetVersion.value)
    state.value.editingBudget = false
    await updateComparison(epoch, sequence)
    if (epoch !== contextEpoch || sequence !== operationSequence) return
    dirty.value = true
    editRevision.value++
    message.value = `当前测算已使用“${activeBudgetVersion.value.name}”。保存为新版本后可保留这次调整。`
  } catch (error) { if (epoch === contextEpoch && sequence === operationSequence) showError(error) }
  finally { if (epoch === contextEpoch && sequence === operationSequence) loading.value = false }
}
async function startBlank() {
  if (!await requestLeave('重新填写预算基准')) return
  const budget = createPlanningScenario(selectedYear.value, selectedCutoff.value)
  scenario.value.budget = budget.budget
  scenario.value.annualTarget = null
  state.value.editingBudget = true
  activeTab.value = 'budget'
  tableMode.value = 'budget'
  validationRequested.value = false
  message.value = '请填写新的月度预算。此前批准版本仍然保留。'
}
async function editBudget() {
  if (!writable.value || !state.value.approved) return
  if (!await requestLeave('编制新的预算版本')) return
  scenario.value.budget = clone(state.value.approved.budget)
  scenario.value.annualTarget = state.value.approved.annualTarget
  state.value.editingBudget = true
  activeTab.value = 'budget'
  validationRequested.value = false
  message.value = '正在编制新的预算版本；原批准预算仍保留，保存后采用新版。'
}
async function cancelBudget() {
  if (!await requestLeave('取消预算修改') || !state.value.approved) return
  scenario.value.budget = clone(state.value.approved.budget)
  scenario.value.annualTarget = state.value.approved.annualTarget
  state.value.editingBudget = false
  validationRequested.value = false
  message.value = '已保留原预算基准。'
}
async function confirmBudget() {
  validationRequested.value = true
  if (!draftResult.value.valid) return
  await savePlan('budget')
}
async function savePlan(kind = 'scenario') {
  if (!writable.value || saving.value || loading.value || hasExpenseChanges.value || (kind !== 'budget' && (!ready.value || !approvedVersion.value))) return
  const name = planName.value.trim().slice(0, 80) || (kind === 'budget' ? `${selectedYear.value}年度预算` : '')
  if (!name) { errorMessage.value = '请先填写方案名称。'; return }
  const timing = startOperation('financial-save', kind)
  let outcome = 'superseded'
  const epoch = contextEpoch
  const sequence = ++operationSequence
  saving.value = true
  errorMessage.value = ''
  try {
    if ((scenario.value.newLease.enabled || scenario.value.costSaving.enabled) && scenario.value.baselineExcludesMeasures) {
      const verified = await client.value.preview(previewPayload())
      if (epoch !== contextEpoch || sequence !== operationSequence) return
      if (financialForecastChanged(sourceForecast.value, verified)) {
        useForecast(verified)
        await updateComparison(epoch, sequence)
        if (epoch !== contextEpoch || sequence !== operationSequence) return
        dirty.value = true
    editRevision.value++
        outcome = 'needs-review'
        message.value = '业务基准已变化，请核对新增租约和降本是否已计入，再保存方案。'
        return
      }
    }
    const response = await client.value.saveVersion({
      ...previewPayload(), name, kind, expected_revision: serverRevision.value,
      ...(kind !== 'budget' ? { budget_version_id: approvedVersion.value.id } : {}),
      client_state: {
        scenario: clone(kind === 'budget' ? scenario.value : currentInput.value), strategy: clone(state.value.strategy),
        leaseOwner: state.value.leaseOwner, savingOwner: state.value.savingOwner,
        contractAdjustments: clone(adjustments.value), budgetVersionId: kind === 'budget' ? null : approvedVersion.value.id
      },
      ...(kind === 'budget' ? { budget_months: scenario.value.budget.map((row, index) => ({ month: index + 1, revenue: Number(row.revenue), operating_cost: Number(row.operatingCost) })) } : {})
    })
    if (epoch !== contextEpoch || sequence !== operationSequence) return
    serverRevision.value = response.revision
    versions.value = [response.version, ...versions.value.filter(item => item.id !== response.version.id)]
    suppressDirty = true
    if (kind === 'budget') {
      activeBudgetVersion.value = response.version
      applyApproved(response.version)
      state.value.editingBudget = false
      validationRequested.value = false
      activeTab.value = 'measures'
    } else {
      if (!await alignBudgetToVersion(response.version, epoch, sequence)) return
      hydrateVersion(response.version)
      if (kind === 'forecast') activeForecastId.value = response.version.id
    }
    savedAt.value = response.version.created_at
    dirty.value = false
    removeStoredDraft()
    message.value = kind === 'budget' ? `“${name}”已保存并冻结为批准预算，历史版本保留。`
      : kind === 'forecast' ? `“${name}”已保存并采用，刷新或重新登录后会载入此版本。`
        : `“${name}”已保存到服务器，可在历史版本中重新载入或采用。`
    outcome = 'ok'
    try { await updateComparison(epoch, sequence) }
    catch (error) {
      outcome = 'degraded'
      if (epoch === contextEpoch && sequence === operationSequence) errorMessage.value = `版本已保存，差异明细暂未读取成功：${error?.message || '请稍后刷新。'}`
    }
  } catch (error) { outcome = 'error'; if (epoch === contextEpoch && sequence === operationSequence) showError(error) }
  finally { suppressDirty = false; if (epoch === contextEpoch && sequence === operationSequence) saving.value = false; timing.finish(outcome) }
}
async function restoreVersion(id) {
  if (!id || !readable.value || loading.value || saving.value) return
  if (!await requestLeave('载入历史版本')) return
  const epoch = contextEpoch
  const sequence = ++operationSequence
  loading.value = true
  errorMessage.value = ''
  try {
    const version = await client.value.version(id)
    if (epoch !== contextEpoch || sequence !== operationSequence) return
    if (version.kind === 'budget') {
      const draft = applyFinancialForecast(createPlanningScenario(version.year), version.snapshot, { seedBudget: true })
      scenario.value.budget = draft.budget
      scenario.value.annualTarget = version.client_state?.scenario?.annualTarget ?? draft.annualTarget
      selectedVersionId.value = version.id
      state.value.editingBudget = writable.value
      activeTab.value = 'budget'
      tableMode.value = 'budget'
      planName.value = version.name
      restoreClientControls(version.client_state)
      savedAt.value = version.created_at
    } else {
      if (!await alignBudgetToVersion(version, epoch, sequence)) return
      hydrateVersion(version)
      await updateComparison(epoch, sequence)
    }
    if (epoch !== contextEpoch || sequence !== operationSequence) return
    dirty.value = false
    message.value = `已载入“${version.name}”。${version.kind === 'budget' ? '月表显示该版本，当前批准版本须通过采用操作切换。' : '采用后将作为下次进入时的默认预测。'}`
  } catch (error) { if (epoch === contextEpoch && sequence === operationSequence) showError(error) }
  finally { if (epoch === contextEpoch && sequence === operationSequence) loading.value = false }
}
async function adoptVersion(id) {
  if (!id || !writable.value || saving.value || loading.value || hasExpenseChanges.value || dirty.value) return
  const epoch = contextEpoch
  saving.value = true
  errorMessage.value = ''
  try {
    const response = await client.value.adoptVersion(id, serverRevision.value)
    if (epoch !== contextEpoch) return
    removeStoredDraft()
    saving.value = false
    const reload = loadWorkspace()
    const reloadEpoch = contextEpoch
    const loaded = await reload
    if (reloadEpoch === contextEpoch && readable.value) message.value = loaded ? `“${response.version.name}”已采用，已重新载入当前版本。` : `“${response.version.name}”已采用，当前版本暂未读取成功，请稍后刷新。`
  } catch (error) { if (epoch === contextEpoch) showError(error) }
  finally { if (epoch === contextEpoch) saving.value = false }
}
function addExpense() {
  if (!writable.value) return
  expenses.value.push(expenseDraft({}, selectedYear.value))
}
async function saveExpense(row) {
  if (!writable.value || saving.value || loading.value) return
  const epoch = contextEpoch
  saving.value = true
  errorMessage.value = ''
  try {
    const saved = await client.value.saveExpense(expenseRequest(row), row.id)
    if (epoch !== contextEpoch) return
    Object.assign(row, expenseDraft(saved, selectedYear.value))
    // A parked copy may still contain a new row without its committed ID.
    // Never restore that stale copy after a confirmed expense write.
    removeStoredDraft()
    dirty.value = true
    editRevision.value++
    saving.value = false
    const refreshed = await refreshData({ preserveExpenseDrafts: true })
    if (epoch === contextEpoch) message.value = refreshed
      ? `费用“${saved.name}”已保存，预测已更新。${needsMeasureConfirmation.value ? '请核对新增租约和降本是否已计入基准。' : ''}`
      : `费用“${saved.name}”已保存，预测暂未刷新成功，请稍后重试。`
  } catch (error) { if (epoch === contextEpoch) showError(error) }
  finally { if (epoch === contextEpoch) saving.value = false }
}
async function toggleComparison(id) {
  if (comparisonIds.value.includes(id)) {
    comparisonIds.value = comparisonIds.value.filter(value => value !== id)
    state.value.saved = state.value.saved.filter(item => item.id !== id)
    return
  }
  if (comparisonIds.value.length >= 3) { message.value = '最多同时比较3个版本，请先取消一个版本。'; return }
  const epoch = contextEpoch
  comparisonIds.value.push(id)
  try {
    const version = await client.value.version(id)
    if (epoch !== contextEpoch || !comparisonIds.value.includes(id)) return
    const input = applyFinancialForecast({ ...createPlanningScenario(version.year), ...clone(version.client_state?.scenario || {}) }, version.snapshot)
    const calculated = calculateBudgetScenario(input)
    state.value.saved.push({ ...version, annual: calculated.annual, nextYear: calculated.nextYear, valid: calculated.valid })
  } catch (error) { if (epoch === contextEpoch) { comparisonIds.value = comparisonIds.value.filter(value => value !== id); showError(error) } }
}
async function useStrategyDemo() {
  if (!await requestLeave('载入资产策略示例')) return
  state.value.strategy = createDemoAssetStrategy()
  state.value.strategyStarted = true
}
async function startStrategy() {
  if (!await requestLeave('清空资产策略')) return
  state.value.strategy = createBlankAssetStrategy()
  state.value.strategyStarted = true
}

function versionKind(kind) { return { budget: '预算', forecast: '预测', scenario: '方案' }[kind] || '版本' }
function timeText(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
function contractTotal(row) { return (row.months || []).reduce((sum, month) => sum + Number(month.revenue || 0), 0) }
const coverageMarkers = computed(() => [
  ['undated_cash_yuan', '待补到账日金额'], ['past_due_rent_balance_yuan', '截止未收租金'], ['expense_payable_yuan', '未付费用']
].map(([key, label]) => ({ key, label, value: Number(sourceForecast.value?.coverage?.[key]) / 10000 })).filter(item => Number.isFinite(item.value) && item.value > 0))
async function inspectPlanningSource(row) {
  const epoch = contextEpoch
  activeTab.value = 'budget'
  await nextTick()
  if (epoch !== contextEpoch) return
  const section = row.contractId ? contractDetails.value : expenseDetails.value
  if (section) { section.open = true; section.scrollIntoView({ block: 'start', behavior: 'smooth' }) }
}
const sourceBridge = computed(() => buildSourcePlanningBase(comparison.value))
const bridge = computed(() => buildSourcePlanningBridge(comparison.value, result.value, sourceBridge.value))
const selectedBridge = computed(() => bridge.value.find(item => item.key === bridgeSelection.value) || bridge.value[0])
const selectedBridgeKey = computed(() => selectedBridge.value?.key)
const selectedBridgeDetails = computed(() => selectedBridge.value?.details)
const drillRows = computed(() => selectedBridgeDetails.value
  ? planningDrillRows({ key: selectedBridgeKey.value, details: selectedBridgeDetails.value }, Number(drillMonth.value)) : [])
const drillTotal = computed(() => drillRows.value.reduce((sum, row) => sum + row.value, 0))
function selectBridge(event) {
  const item = bridge.value[event?.dataIndex]
  if (item) { bridgeSelection.value = item.key; drillMonth.value = 0 }
}
const bridgeOption = computed(() => {
  const values = bridge.value
  const bars = planningBridgeBars(values)
  const data = (key) => bars.map((bar, index) => ({
    value: bar[key],
    itemStyle: { color: values[index].total ? '#3b82f6' : values[index].value >= 0 ? '#10b981' : '#d97706', borderColor: '#94a3b8', borderWidth: values[index].key === bridgeSelection.value ? 2 : 0 },
    label: { show: key === 'positive' ? bar.positive > 0 : bar.positive === 0 }
  }))
  return {
    animationDurationUpdate: 220,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (items) => {
      const item = values[items[0]?.dataIndex]
      return item ? `${item.label}<br/>${item.total ? numberText(item.value) : signed(item.value)} 万元` : ''
    } },
    grid: { left: 10, right: 12, top: 35, bottom: 12, containLabel: true },
    xAxis: { type: 'category', data: values.map(item => item.label.replace(/(.{2,4})(.{2,4})$/, '$1\n$2')), axisLabel: { interval: 0, color: '#64748b', fontSize: 11 }, axisLine: { lineStyle: { color: '#cbd5e1' } } },
    yAxis: { type: 'value', name: '万元', axisLabel: { color: '#64748b' }, splitLine: { lineStyle: { color: '#e5e7eb' } } },
    series: [
      { name: '起点', type: 'bar', stack: 'bridge', silent: true, tooltip: { show: false }, itemStyle: { color: 'transparent' }, emphasis: { disabled: true }, data: bars.map(bar => bar.offset) },
      ...['positive', 'negative'].map(key => ({
        name: '贡献', type: 'bar', stack: 'bridge', barMaxWidth: 44, data: data(key),
        label: { position: key === 'positive' ? 'top' : 'bottom', color: '#475569', fontSize: 10, formatter: ({ dataIndex }) => values[dataIndex].total ? numberText(values[dataIndex].value, 0) : signed(values[dataIndex].value) }
      }))
    ]
  }
})

const chartOption = computed(() => {
  if (!ready.value) return {}
  const rows = result.value.months
  const axis = { type: 'value', name: '万元', axisLabel: { color: '#64748b' }, splitLine: { lineStyle: { color: '#e5e7eb' } } }
  const line = (name, color, data, dashed = false) => ({
    name, type: 'line', smooth: 0.2, symbol: 'circle', symbolSize: 5,
    lineStyle: { width: 3, type: dashed ? 'dashed' : 'solid' }, itemStyle: { color }, data
  })
  return {
    animationDurationUpdate: 280,
    tooltip: { trigger: 'axis', valueFormatter: (value) => `${numberText(value)} 万元` },
    legend: { top: 0, left: 0, itemWidth: 20, textStyle: { color: '#475569' } },
    grid: { left: 18, right: 24, top: 66, bottom: 18, containLabel: true },
    xAxis: { type: 'category', data: rows.map((row) => `${row.month}月`), axisLine: { lineStyle: { color: '#cbd5e1' } }, axisLabel: { color: '#64748b' } },
    yAxis: axis,
    series: chartMetric.value === 'ebitda' ? [
      line('批准预算 EBITDA', '#94a3b8', rows.map((row) => row.budgetEbitda), true),
      line('全年基准预计', '#3b82f6', rows.map((row) => row.baselineEbitda)),
      line('措施后预计', '#10b981', rows.map((row) => row.withMeasuresEbitda)),
      { name: '已完成实际', type: 'line', lineStyle: { opacity: 0 }, showSymbol: true, symbolSize: 9, itemStyle: { color: '#1d4ed8' }, data: rows.map((row) => row.actualEbitda) }
    ] : [
      { name: '新增租约收入', type: 'bar', barMaxWidth: 26, itemStyle: { color: '#3b82f6', borderRadius: [3, 3, 0, 0] }, data: rows.map((row) => row.newLeaseRevenue) },
      line('新增租约现金收款', '#d97706', rows.map((row) => row.newLeaseCash)),
      line('全部措施现金增量', '#10b981', rows.map((row) => row.cashIncrement))
    ]
  }
})

const strategyChart = computed(() => strategyReady.value ? {
  tooltip: { trigger: 'axis', valueFormatter: (value) => `${numberText(value)} 万元` },
  grid: { left: 8, right: 65, top: 20, bottom: 12, containLabel: true },
  xAxis: { type: 'value', name: '万元', axisLabel: { color: '#64748b' }, splitLine: { lineStyle: { color: '#e5e7eb' } } },
  yAxis: { type: 'category', inverse: true, data: strategyResult.value.strategies.map((item) => item.label), axisLabel: { color: '#475569' }, axisTick: { show: false }, axisLine: { show: false } },
  series: [{ type: 'bar', barMaxWidth: 24, label: { show: true, position: 'right', color: '#475569', formatter: ({ value }) => numberText(value) }, data: strategyResult.value.strategies.map((item, index) => ({ value: item.npv, itemStyle: { color: ['#3b82f6', '#10b981', '#d97706'][index], borderRadius: [0, 4, 4, 0] } })) }]
} : {})
</script>

<template>
  <section class="decision-workbench" aria-label="预算与项目策略工作区">
    <header class="dw-heading">
      <div><p class="dw-eyebrow">{{ scopeLabel }} · 经营推演</p><h2>把全年目标，落到每一次经营动作</h2></div>
      <span v-if="readable" class="dw-session-note" :class="{ 'is-saved': savedAt && !dirty && !hasExpenseChanges }" data-testid="planning-save-status">{{ hasExpenseChanges ? '有未保存的费用修改' : dirty ? '有未保存的预测调整' : savedAt ? `已保存 · ${timeText(savedAt)}` : '合同与费用驱动预测' }}</span>
    </header>

    <div v-if="!readable" class="dw-empty" data-testid="planning-scope-unavailable">
      <h3>进入项目，查看预算与预测</h3><p>请选择具备经营明细权限的项目，使用合同、费用和已保存的方案继续分析。</p>
    </div>
    <template v-else>
      <div class="dw-planning-toolbar">
        <label>年度<input  :value="selectedYear" type="number" min="2000" max="2100" step="1" data-testid="planning-year" :disabled="loading || saving" @change="changeYear" /></label>
        <label>实际截止月<select v-model.number="selectedCutoff" data-testid="planning-cutoff" :disabled="loading || saving" @change="calculateContractChanges"><option :value="0">尚未结账</option><option v-for="month in months" :key="month" :value="month">{{ month }}月末</option></select></label>
        <label>方案名称<input v-model="planName" type="text" maxlength="80" placeholder="例如：9月经营预测" data-testid="planning-name" :disabled="loading || saving" /></label>
        <div class="dw-actions"><button type="button" :disabled="loading || saving" data-testid="planning-refresh" @click="refreshData">{{ loading ? '读取中…' : '刷新业务数据' }}</button><button type="button" class="dw-primary" :disabled="!writable || !ready || !approvedVersion || !planName.trim() || hasExpenseChanges || loading || saving" data-testid="planning-save-forecast" @click="savePlan('forecast')">{{ saving ? '保存中…' : '保存并采用预测' }}</button></div>
      </div>
      <div class="dw-version-controls">
        <button type="button" :disabled="loading || saving || (!dirty && !hasExpenseChanges)" data-testid="planning-park-draft" @click="parkDraftFromButton">暂存草稿</button>
        <label>已保存版本<select :value="selectedVersionId || ''" data-testid="planning-version-select" :disabled="loading || saving" @change="selectHistory"><option value="">选择已保存的预算、预测或方案</option><option v-for="version in versions" :key="version.id" :value="version.id">{{ version.name }} · {{ versionKind(version.kind) }} · {{ timeText(version.created_at) }}{{ version.id === activeBudgetVersion?.id ? ' · 已批准' : version.id === activeForecastId ? ' · 已采用' : '' }}</option></select></label>
        <button type="button" :disabled="!writable || !selectedVersionId || dirty || hasExpenseChanges || loading || saving" data-testid="planning-adopt" @click="adoptVersion(selectedVersionId)">采用此已存版本</button>
        <button type="button" :disabled="!writable || !ready || !approvedVersion || !planName.trim() || hasExpenseChanges || loading || saving" data-testid="planning-save-scenario" @click="savePlan('scenario')">另存具名方案</button>
      </div>
      <p class="dw-readonly-notice" data-testid="planning-draft-limit">暂存草稿仅在本次页面内保留，刷新或关闭页面后会丢失；不会自动采用预测、冻结预算或保存费用。</p>
      <div v-if="state.approved || activeBudgetVersion" class="dw-budget-link" data-testid="planning-budget-link">本方案预算：<strong>{{ approvedVersion?.name || (state.approved ? '历史保存预算' : '尚未选择') }}</strong><template v-if="activeBudgetVersion && approvedVersion?.id !== activeBudgetVersion.id"><span> · 当前批准预算：{{ activeBudgetVersion.name }}</span><button type="button" class="dw-text-button" data-testid="planning-use-current-budget" :disabled="loading || saving" @click="useActiveBudget">使用当前批准预算</button></template><span v-if="state.approved && !approvedVersion"> · 此历史方案未关联预算来源，按保存数字展示。</span></div>
      <p v-if="!writable && !loading" class="dw-readonly-notice">当前为只读权限，可以查看和调整测算假设；保存版本及修改费用需要项目编辑权限。</p>
      <p v-if="hasExpenseChanges" class="dw-readonly-notice">费用明细有未保存修改。请先保存费用，再保存预算或预测版本。</p>
      <p v-if="errorMessage" class="dw-planning-error" role="alert" data-testid="planning-error">{{ errorMessage }}</p>
      <p v-if="loading && !sourceForecast" class="dw-loading" role="status">正在读取项目合同、费用和已保存版本…</p>
      <FinancialCoverage v-if="sourceForecast" :coverage="sourceForecast.coverage" @open-contract="emit('open-contract', $event)" />
      <fieldset class="dw-editable-area" :disabled="loading || saving">

    <div class="dw-tabs" role="tablist" aria-label="经营推演主题">
      <button id="dw-budget-tab" type="button" role="tab" :aria-selected="activeTab === 'budget'" aria-controls="dw-budget-panel" :class="{ active: activeTab === 'budget' }" @click="activeTab = 'budget'">月度预算</button>
      <button id="dw-measures-tab" type="button" role="tab" :aria-selected="activeTab === 'measures'" aria-controls="dw-measures-panel" :class="{ active: activeTab === 'measures' }" @click="activeTab = 'measures'">经营措施 <span v-if="ready">{{ gapText(annual.targetGapAfter) }}</span></button>
      <button id="dw-strategy-tab" type="button" role="tab" :aria-selected="activeTab === 'strategy'" aria-controls="dw-strategy-panel" :class="{ active: activeTab === 'strategy' }" @click="activeTab = 'strategy'">持有与项目策略</button>
    </div>
    <p v-if="message" class="dw-message" role="status" data-testid="planning-notice">{{ message }}</p>

    <div v-if="activeTab !== 'strategy' && !state.started" class="dw-empty">
      <span class="dw-step-marker">01 → 12</span><h3>预算随经营节奏展开</h3>
      <p>逐月安排收入与费用，固定预算基准；再调整起租、免租和降本措施，查看全年目标与现金的变化。</p>
      <div class="dw-actions"><button type="button" class="dw-primary" @click="refreshData">读取合同基准</button><button type="button" :disabled="!writable" @click="startBlank">填写预算基准</button></div>

    </div>

    <template v-else-if="activeTab !== 'strategy'">
      <div v-if="state.approved" class="dw-outcome-strip">
        <div><span>批准年度目标 · EBITDA</span><strong>{{ numberText(state.approved.annualTarget) }}<small>万元</small></strong></div>
        <div><span>全年基准预计</span><strong>{{ numberText(basisResult.annual?.baselineForecast) }}<small>万元</small></strong></div>
        <div class="dw-outcome-highlight"><span>措施后全年预计</span><strong>{{ numberText(annual?.withMeasures) }}<small>万元</small></strong></div>
        <div :class="{ 'is-shortfall': annual && annual.targetGapAfter > 0, 'is-achieved': annual && annual.targetGapAfter <= 0 }"><span>对比年度目标</span><strong class="dw-gap">{{ gapText(annual?.targetGapAfter) }}<small v-if="annual && annual.targetGapAfter !== 0">万元</small></strong></div>
      </div>

      <section v-if="activeTab === 'budget'" id="dw-budget-panel" class="dw-panel" role="tabpanel" aria-labelledby="dw-budget-tab">
        <header class="dw-section-heading"><div><h3>{{ selectedVersion?.kind === 'budget' && selectedVersionId !== approvedVersion?.id ? '查看历史预算' : state.editingBudget ? '编制月度预算' : '当前预算基准' }}</h3><p>合同管理口径。收入按租期确认，费用按发生月计入；回款按到账日期列示。预算冻结后，未来预测可继续更新。</p></div><button v-if="!state.editingBudget && writable" type="button" @click="editBudget">编制新预算版本</button></header>
        <details ref="contractDetails" class="dw-source-details" data-testid="planning-contract-details">
          <summary>合同自动基准 · {{ sourceForecast ? `${contractRows.length} 份合同` : '待载入' }}</summary>
          <div class="dw-source-details-body">
            <div class="dw-source-summary">
              <span>租金收入 <strong data-testid="planning-source-rent-income">{{ numberText(sourceForecast?.totals?.rent_income) }}</strong> 万元</span><span>物业费收入 <strong>{{ numberText(sourceForecast?.totals?.property_fee_income) }}</strong> 万元</span><span>租金应收 <strong>{{ numberText(sourceForecast?.totals?.rent_receivable) }}</strong> 万元</span><span>物业费应收 <strong>{{ numberText(sourceForecast?.totals?.property_fee_receivable) }}</strong> 万元</span>
              <span>实际到账 <strong data-testid="planning-source-actual-cash">{{ numberText(sourceForecast?.totals?.actual_cash_received) }}</strong> 万元</span><span>后续预计收款 <strong>{{ numberText(sourceForecast?.totals?.forecast_cash_received) }}</strong> 万元</span><span>经营费用 <strong>{{ numberText(sourceForecast?.totals?.operating_cost) }}</strong> 万元</span><span>资本支出 <strong>{{ numberText(sourceForecast?.totals?.capex) }}</strong> 万元</span><span>净现金流 <strong>{{ numberText(sourceForecast?.totals?.net_cash_flow) }}</strong> 万元</span>
            </div>
            <p v-if="coverageMarkers.length" class="dw-source-coverage"><span v-for="item in coverageMarkers" :key="item.key">{{ item.label }} <b>{{ numberText(item.value) }}</b> 万元</span></p>
            <div class="dw-comparison-scroll"><table class="dw-comparison-table dw-source-table"><thead><tr><th>合同 / 租户</th><th>租期</th><th>年度收入 · 万元</th><th>起租顺延 · 月</th><th>租金变化 · %</th><th>明细</th></tr></thead><tbody><tr v-for="row in contractRows" :key="row.source_key"><th>{{ row.contract_no || row.label }}<small class="dw-version-caption">{{ row.tenant_name }} {{ row.room_no }}</small></th><td>{{ row.start_date || row.source_snapshot?.start_date }}<br/>{{ row.end_date || row.source_snapshot?.end_date }}</td><td class="dw-numeric">{{ numberText(contractTotal(row)) }}</td><td><input v-model.number="row.start_shift_months" type="number" min="-24" max="24" step="1" :aria-label="`${row.label}起租顺延月数`" :data-testid="`planning-contract-shift-${row.contract_id}`" :disabled="loading || saving" /></td><td><input v-model.number="row.rent_change_percent" type="number" min="-100" max="500" step="0.5" :aria-label="`${row.label}租金变化百分比`" :data-testid="`planning-contract-rent-${row.contract_id}`" :disabled="loading || saving" /></td><td><button type="button" class="dw-text-button" @click="emit('open-contract', row.contract_id)">打开合同</button></td></tr><tr v-if="!contractRows.length"><td colspan="6">{{ sourceForecast ? '当前年度没有纳入计算的合同。' : '预测尚未读取，请刷新业务数据后重试。' }}</td></tr></tbody></table></div>
            <div class="dw-expense-footer"><p>起租和租金假设用于本方案，不修改正式合同。</p><button type="button" :disabled="loading || saving" data-testid="planning-preview-contracts" @click="calculateContractChanges">按合同假设重算</button></div>
          </div>
        </details>
        <details ref="expenseDetails" class="dw-source-details" data-testid="planning-expense-details">
          <summary>费用明细 · {{ expenses.length }} 项</summary>
          <div class="dw-source-details-body"><div class="dw-comparison-scroll"><table class="dw-comparison-table dw-source-table dw-expense-table"><thead><tr><th>费用名称</th><th>类别</th><th>金额 · 万元</th><th>发生月</th><th>计划支付月</th><th>实际支付日</th><th>责任人</th><th>有效</th><th>操作</th></tr></thead><tbody><tr v-for="(row, index) in expenses" :key="row.id || `new-${index}`"><td><input v-model="row.name" class="dw-expense-name" type="text" maxlength="120" :aria-label="`第${index + 1}项费用名称`" :disabled="!writable || saving || loading" /></td><td><select v-model="row.category" :aria-label="`第${index + 1}项费用类别`" :disabled="!writable || saving || loading"><option value="opex">经营费用</option><option value="capex">资本支出</option></select></td><td><input v-model.number="row.amount" type="number" min="0" step="0.01" :aria-label="`第${index + 1}项费用金额，万元`" :disabled="!writable || saving || loading" /></td><td><input v-model="row.occurrence_month" type="month" :aria-label="`第${index + 1}项费用发生月`" :disabled="!writable || saving || loading" /></td><td><input v-model="row.payment_month" type="month" :aria-label="`第${index + 1}项费用计划支付月`" :disabled="!writable || saving || loading" /></td><td><input v-model="row.paid_on" type="date" :aria-label="`第${index + 1}项费用实际支付日`" :disabled="!writable || saving || loading" /></td><td><input v-model="row.owner" type="text" maxlength="80" :aria-label="`第${index + 1}项费用责任人`" :disabled="!writable || saving || loading" /></td><td><label class="dw-checkbox"><input v-model="row.is_active" type="checkbox" :aria-label="`第${index + 1}项费用有效`" :disabled="!writable || saving || loading" /></label></td><td><button type="button" :disabled="!writable || saving || loading" :data-testid="`planning-expense-save-${index}`" @click="saveExpense(row)">保存费用</button><button v-if="!row.id" type="button" class="dw-text-button" :disabled="saving || loading" @click="expenses.splice(index, 1)">取消新增</button></td></tr><tr v-if="!expenses.length"><td colspan="9">尚无费用明细，可按发生月新增经营费用或资本支出。</td></tr></tbody></table></div><div class="dw-expense-footer"><p>实际支付日留空表示未实付。资本支出影响现金，不直接计入 EBITDA。</p><button v-if="writable" type="button" data-testid="planning-expense-add" :disabled="saving || loading" @click="addExpense">新增费用</button></div></div>
        </details>
        <div class="dw-basis-meta">
          <label>预算年度<strong>{{ selectedYear }} 年</strong></label>
          <label>合同及费用数据<strong>{{ sourceForecast ? timeText(sourceForecast.generated_at) : '待载入' }}</strong></label>
          <label>批准年度 EBITDA 目标（万元）<input v-model.number="scenario.annualTarget" type="number" step="0.1" placeholder="可填负值或0" data-testid="planning-annual-target" :disabled="!state.editingBudget || !writable || loading || saving" /></label>
          <div class="dw-basis-total"><span>12个月预算合计</span><strong>{{ numberText(draftBudgetTotal) }} <small>万元</small></strong><span>{{ draftBudgetGap !== null ? gapText(draftBudgetGap) : '填完月度预算后核对' }}</span></div>
        </div>
        <div class="dw-table-toolbar"><div class="dw-segmented"><button type="button" :class="{ active: tableMode === 'budget' }" @click="tableMode = 'budget'">批准月度预算</button><button type="button" :class="{ active: tableMode === 'latest' }" @click="tableMode = 'latest'">实际与剩余月份预计</button></div></div>
        <p class="dw-table-caption">{{ tableMode === 'budget' ? '预算按月编制，年度目标独立保留。保存后冻结，并保留历史版本。' : (Number(scenario.closedThroughMonth) ? `1—${scenario.closedThroughMonth}月为合同管理口径的已发生期间，其余为最新预测。` : '1—12月为最新预测。') + '调整合同假设或费用后重新计算，另行叠加的经营措施不重复计入。' }}</p>
        <div class="dw-month-table-wrap"><table class="dw-month-table"><thead><tr><th scope="col">月份</th><th scope="col">口径</th><th scope="col">收入 · 万元</th><th scope="col">经营费用 · 万元</th><th scope="col">EBITDA · 万元</th></tr></thead><tbody>
          <tr v-for="(month, index) in months" :key="month" :class="{ 'is-closed': tableMode === 'latest' && month <= Number(scenario.closedThroughMonth) }">
            <th scope="row">{{ month }}月</th><td><span class="dw-row-kind">{{ tableMode === 'budget' ? '批准预算' : month <= Number(scenario.closedThroughMonth) ? '已完成实际' : '基准预计' }}</span></td>
            <template v-if="tableMode === 'budget'"><td><input v-model.number="scenario.budget[index].revenue" type="number" min="0" step="0.1" :aria-label="`${month}月预算收入，万元`" :data-testid="`planning-budget-revenue-${month}`" placeholder="未填" :disabled="!state.editingBudget || !writable || loading || saving" /></td><td><input v-model.number="scenario.budget[index].operatingCost" type="number" min="0" step="0.1" :aria-label="`${month}月预算经营费用，万元`" :data-testid="`planning-budget-cost-${month}`" placeholder="未填" :disabled="!state.editingBudget || !writable || loading || saving" /></td><td class="dw-numeric">{{ numberText(rowEbitda(scenario.budget[index])) }}</td></template>
            <template v-else><td class="dw-numeric" :data-testid="`planning-latest-revenue-${month}`">{{ numberText(latestRow(index).revenue) }}</td><td class="dw-numeric">{{ numberText(latestRow(index).operatingCost) }}</td><td class="dw-numeric">{{ numberText(rowEbitda(latestRow(index))) }}</td></template>
          </tr>
        </tbody></table></div>
        <div v-if="state.editingBudget" class="dw-budget-footer"><div><p>批准预算冻结后，经营措施单独计算后续月份贡献。</p><label class="dw-checkbox"><input v-model="scenario.baselineExcludesMeasures" type="checkbox" />基准预计未重复包含下方新增租约和降本措施</label></div><div class="dw-actions"><button v-if="state.approved" type="button" :disabled="saving || loading" @click="cancelBudget">取消修改</button><button type="button" class="dw-primary" data-testid="planning-freeze-budget" :disabled="!writable || hasExpenseChanges || saving || loading" @click="confirmBudget">保存并冻结预算</button></div></div>
        <div v-if="validationRequested && !draftResult.valid" class="dw-errors" role="alert"><strong>请补齐或修正以下内容</strong><ul><li v-for="error in draftResult.errors.slice(0, 6)" :key="error">{{ error }}</li></ul><span v-if="draftResult.errors.length > 6">另有 {{ draftResult.errors.length - 6 }} 项待填写。</span></div>
      </section>

      <section v-if="activeTab === 'measures'" id="dw-measures-panel" role="tabpanel" aria-labelledby="dw-measures-tab">
        <div v-if="!state.approved" class="dw-empty dw-empty-compact"><h3>先填写一个预算基准</h3><p>填完月度预算和基准预计后，即可调整经营措施。</p><button type="button" class="dw-primary" @click="activeTab = 'budget'">填写并保存预算基准</button></div>
        <template v-else>
          <div class="dw-baseline-caption"><span>{{ selectedYear }}年 · 合同管理口径 · 实际截至{{ scenario.closedThroughMonth ? `${scenario.closedThroughMonth}月末` : '尚未结账' }}</span><span>累计实际 EBITDA <b>{{ numberText(basisResult.annual?.actualToDate) }}</b> 万元</span><span>较同期预算 <b>{{ signed(actualVariance) }}</b> 万元</span><span>剩余月份预计 <b>{{ numberText(basisResult.annual?.remainingForecast) }}</b> 万元</span></div>
          <div class="dw-measures-layout">
            <div class="dw-chart-column"><div class="dw-panel dw-main-chart"><header class="dw-section-heading"><div><h3>{{ chartMetric === 'ebitda' ? '目标缺口，怎样逐月补上' : '收入与现金，分别何时发生' }}</h3><p>{{ chartMetric === 'ebitda' ? '历史实际固定，蓝线为当前基准，绿线随措施变化。' : '仅展示新增措施的增量；与项目全部现金流分别展示。' }}</p></div></header><div class="dw-segmented"><button type="button" :class="{ active: chartMetric === 'ebitda' }" @click="chartMetric = 'ebitda'">月度 EBITDA</button><button type="button" :class="{ active: chartMetric === 'cash' }" @click="chartMetric = 'cash'">新增收入与现金</button></div><EChartPanel :option="chartOption" :height="310" :empty="!ready" empty-text="请补齐右侧已启用措施的参数" aria-label="月度预算、基准预计与经营措施对比" /></div>
              <div class="dw-impact-row"><div><span>今年 EBITDA 增量</span><strong>{{ signed(annual?.ebitdaIncrement) }} <small>万元</small></strong></div><div><span>今年现金增量</span><strong>{{ signed(annual?.cashIncrement) }} <small>万元</small></strong></div><div><span>次年 EBITDA 增量</span><strong>{{ signed(ready ? result.nextYear.ebitdaIncrement : null) }} <small>万元</small></strong></div><div><span>次年现金增量</span><strong>{{ signed(ready ? result.nextYear.cashIncrement : null) }} <small>万元</small></strong></div></div>
              <section class="dw-panel dw-bridge-panel" data-testid="planning-variance-bridge">
                <header class="dw-section-heading"><div><h3>预算到预测，差异来自哪里</h3><p>合同、费用与预算编制调整逐项加总，再叠加经营措施。点击柱形或下方项目查看明细。</p></div></header>
                <EChartPanel :option="bridgeOption" :height="280" :empty="!bridge.length" empty-text="完成预算和预测后展示来源差异" aria-label="批准预算到最新预测的差异桥" @chart-click="selectBridge" />
                <div class="dw-bridge-buttons"><button v-for="item in bridge" :key="item.key" type="button" :class="{ active: bridgeSelection === item.key }" :aria-pressed="bridgeSelection === item.key" :data-testid="`planning-bridge-${item.key}`" @click="bridgeSelection = item.key; drillMonth = 0">{{ item.label }}</button></div>
                <div v-if="selectedBridge" v-memo="[selectedBridgeKey, selectedBridgeDetails, drillMonth]" class="dw-drilldown" data-testid="planning-drilldown"><div class="dw-drill-heading"><h4>{{ selectedBridge.label }} · 来源明细</h4><label class="dw-sr-only" for="planning-drill-month">差异明细月份</label><select id="planning-drill-month" v-model.number="drillMonth"><option :value="0">全年</option><option v-for="month in months" :key="month" :value="month">{{ month }}月</option></select><strong data-testid="planning-drill-total">合计 {{ signed(drillTotal) }} 万元</strong></div><div class="dw-comparison-scroll"><table class="dw-comparison-table dw-source-table"><thead><tr><th>来源</th><th>变化说明</th><th>预算 · 万元</th><th>当前 · 万元</th><th>本项贡献 · 万元</th><th>明细</th></tr></thead><tbody><tr v-for="row in drillRows" :key="row.key"><th>{{ row.label }}</th><td>{{ row.reason }}</td><td class="dw-numeric">{{ numberText(row.baseline) }}</td><td class="dw-numeric">{{ numberText(row.current) }}</td><td class="dw-numeric" :data-testid="`planning-drill-value-${row.key}`">{{ signed(row.value) }}</td><td><template v-if="row.contractId"><button type="button" class="dw-text-button" @click="emit('open-contract', row.contractId)">打开合同</button><button type="button" class="dw-text-button" @click="inspectPlanningSource(row)">调整假设</button></template><button v-else-if="row.sourceType === 'expense'" type="button" class="dw-text-button" @click="inspectPlanningSource(row)">查看费用</button><span v-else>—</span></td></tr><tr v-if="!drillRows.length"><td colspan="6">该期间没有金额变化。</td></tr></tbody></table></div></div>
              </section>
              <p class="dw-footnote">次年仅计算这些措施的增量，未预测次年全部经营结果。收入按含免租期的租期直线确认；收款按免租期后月租及收款延迟计算。</p>
              <details class="dw-assumptions"><summary>查看本次计算口径</summary><ul><li v-for="item in result.assumptions" :key="item">{{ item }}</li></ul><p>降本应以不影响必要服务与安全为前提。回款提前不等于收入增加；利息、所得税、折旧和摊销不计入此处 EBITDA。</p></details>
            </div>
            <div class="dw-controls-column">
              <template v-if="snapshotVersionId && result.valid && !dirty && !hasExpenseChanges">
                <ExecutionActions v-if="scenario.newLease.enabled" :version-id="snapshotVersionId" measure-key="newLease" :building-code="buildingCode" compact @open-contract="emit('open-contract',$event)" />
                <ExecutionActions v-if="scenario.costSaving.enabled" :version-id="snapshotVersionId" measure-key="costSaving" :building-code="buildingCode" compact @open-contract="emit('open-contract',$event)" />
              </template>
              <p v-else-if="scenario.newLease.enabled || scenario.costSaving.enabled" class="dw-footnote">保存预测或具名方案后，可为本版措施建立执行记录；任务完成与收益核验分别登记。</p>
              <section v-if="scenario.newLease.enabled || scenario.costSaving.enabled" class="dw-control-group dw-measure-confirmation" :class="{ 'needs-confirmation': needsMeasureConfirmation }"><label class="dw-checkbox"><input v-model="scenario.baselineExcludesMeasures" type="checkbox" data-testid="planning-measures-confirm" />已核对，基准未包含下方启用措施</label><p class="dw-footnote">{{ needsMeasureConfirmation ? '请核对新增租约和降本是否已计入基准；已纳入的措施应关闭，避免重复计算。' : '业务基准变化后，请重新核对措施是否仍需单独叠加。' }}</p></section>
              <section class="dw-control-group"><label class="dw-control-toggle"><span><b>新增租约</b><small>调整拟议条款，观察起租时点</small></span><input v-model="scenario.newLease.enabled" type="checkbox" aria-label="启用新增租约" /></label><fieldset :disabled="!scenario.newLease.enabled"><div class="dw-fields-two"><label>开始租赁月份<select v-model.number="scenario.newLease.startMonth"><option :value="null">请选择</option><option v-for="month in availableStartMonths" :key="month" :value="month">{{ monthLabel(month) }}</option></select></label><label>免租期（月）<input v-model.number="scenario.newLease.freeMonths" type="number" min="0" max="119" step="1" placeholder="含0个月" /></label><label>租期（月，含免租）<input v-model.number="scenario.newLease.termMonths" type="number" min="1" max="120" step="1" /></label><label>收款延迟（月）<input v-model.number="scenario.newLease.collectionDelayMonths" type="number" min="0" max="12" step="1" placeholder="0为当月收款" /></label></div><label class="dw-slider-label">免租期后月租（万元）<input v-model.number="scenario.newLease.monthlyRent" type="number" min="0" step="0.5" /></label><input class="dw-range" type="range" min="0" max="100" step="0.5" :value="scenario.newLease.monthlyRent ?? 0" aria-label="调整月租，万元" @input="scenario.newLease.monthlyRent = Number($event.target.value)" /><div class="dw-range-labels"><span>0 万元</span><span>100 万元</span></div><label>推进人（选填）<input v-model="state.leaseOwner" type="text" maxlength="40" placeholder="用于现场讨论" /></label></fieldset></section>
              <section class="dw-control-group"><label class="dw-control-toggle"><span><b>经营降本</b><small>按生效月份计算，扣除实施费用</small></span><input v-model="scenario.costSaving.enabled" type="checkbox" aria-label="启用经营降本" /></label><fieldset :disabled="!scenario.costSaving.enabled"><div class="dw-fields-two"><label>开始生效月份<select v-model.number="scenario.costSaving.startMonth"><option :value="null">请选择</option><option v-for="month in availableStartMonths" :key="month" :value="month">{{ monthLabel(month) }}</option></select></label><label>一次性实施费（万元）<input v-model.number="scenario.costSaving.implementationCost" type="number" min="0" step="0.5" placeholder="无费用请填0" /></label></div><label class="dw-slider-label">每月节约（万元）<input v-model.number="scenario.costSaving.monthlySaving" type="number" min="0" step="0.5" /></label><input class="dw-range" type="range" min="0" :max="maximumMonthlySaving" step="0.5" :value="scenario.costSaving.monthlySaving ?? 0" aria-label="调整每月节约金额，万元" @input="scenario.costSaving.monthlySaving = Number($event.target.value)" /><div class="dw-range-labels"><span>0 万元</span><span>{{ numberText(maximumMonthlySaving, 0) }} 万元</span></div><label>推进人（选填）<input v-model="state.savingOwner" type="text" maxlength="40" placeholder="用于现场讨论" /></label></fieldset></section>
              <div v-if="!result.valid" class="dw-errors" role="alert"><ul><li v-for="error in result.errors.slice(0, 5)" :key="error">{{ error }}</li></ul></div>
            </div>
          </div>
          <section class="dw-panel dw-plan-comparison"><header class="dw-section-heading"><div><h3>已保存版本，放在一起比较</h3><p>从已保存版本中选择最多3个方案，比较当时保存的经营措施与年度结果。</p></div></header><div class="dw-bridge-buttons"><button v-for="version in versions.filter(item => item.kind !== 'budget')" :key="version.id" type="button" :class="{ active: comparisonIds.includes(version.id) }" :aria-pressed="comparisonIds.includes(version.id)" @click="toggleComparison(version.id)">{{ version.name }}<span v-if="version.id === activeForecastId" class="dw-version-status">已采用</span></button></div><div v-if="!state.saved.length" class="dw-comparison-empty">保存具名方案后，可选择版本并列比较。刷新后仍可从服务器重新载入。</div><div v-else class="dw-comparison-scroll"><table class="dw-comparison-table"><thead><tr><th scope="col">方案</th><th scope="col">全年 EBITDA</th><th scope="col">目标缺口</th><th scope="col">今年现金增量</th><th scope="col">次年 EBITDA 增量</th><th scope="col">操作</th></tr></thead><tbody><tr v-for="plan in state.saved" :key="plan.id"><th scope="row">{{ plan.name }}<span class="dw-version-caption">{{ timeText(plan.created_at) }} · {{ plan.created_by }}</span></th><td>{{ numberText(plan.annual?.withMeasures) }}</td><td :class="{ 'dw-negative': plan.annual?.targetGapAfter > 0 }">{{ gapText(plan.annual?.targetGapAfter) }}</td><td>{{ signed(plan.annual?.cashIncrement) }}</td><td>{{ signed(plan.nextYear?.ebitdaIncrement) }}</td><td><div class="dw-version-actions"><button type="button" class="dw-text-button" @click="restoreVersion(plan.id)">载入</button><button type="button" class="dw-text-button" :disabled="!writable || hasExpenseChanges || dirty" @click="adoptVersion(plan.id)">采用</button></div></td></tr></tbody></table></div><p class="dw-footnote">金额单位：万元。每个版本同时保留预算、预测、经营措施和资产策略参数；不同年度目标以各自保存值比较。</p></section>
        </template>
      </section>
    </template>

    <section v-if="activeTab === 'strategy'" id="dw-strategy-panel" role="tabpanel" aria-labelledby="dw-strategy-tab">
      <div v-if="!state.strategyStarted" class="dw-empty dw-empty-compact"><span class="dw-step-marker">持有 / 改造 / 出售</span><h3>在同一期间，比较项目的不同路径</h3><p>补齐经营现金流、改造、售价与费用假设，再计算各路径的净现值。</p><div class="dw-actions"><button type="button" class="dw-primary" @click="useStrategyDemo">使用初始策略</button><button type="button" @click="startStrategy">填写项目假设</button></div></div>
      <template v-else>

        <ForecastStrategyLink v-model="state.strategy" :versions="versions" :building-code="buildingCode" :disabled="!writable || loading || saving" />
        <div class="dw-panel dw-strategy-context"><label>项目经营方式<select v-model="state.strategy.propertyType"><option value="own">自持资产</option><option value="leased">租入运营</option><option value="managed">受托管理</option></select></label><label v-if="state.strategy.propertyType === 'own'" class="dw-checkbox"><input v-model="state.strategy.hasDisposalRight" type="checkbox" />纳入资产出售路径</label><p v-if="state.strategy.propertyType === 'leased'">租入运营应比较续租、调租、经营改造和退出承租；资产出售须由产权方决定。</p><p v-else-if="state.strategy.propertyType === 'managed'">受托管理应比较管理续约、收费调整和退出管理；本工作区不将受托项目视为可出售资产。</p><p v-else>自持资产可比较继续持有、改造后持有和现在出售。</p></div>
        <template v-if="state.strategy.propertyType === 'own' && state.strategy.hasDisposalRight">
          <div class="dw-strategy-assumptions"><label>统一比较期间（年）<input v-model.number="state.strategy.years" type="number" min="1" max="30" step="1" /></label><label>年折现率（%）<input v-model.number="state.strategy.discountRate" type="number" min="0" max="100" step="0.5" /></label><p>比较时点统一为今天。持有与改造在期末出售，现金流折算回今天；按资产层计算，不含融资与所得税。</p></div>
          <section v-if="strategyReady" class="dw-panel dw-strategy-summary" aria-label="三种项目策略净现值摘要">
            <p class="dw-strategy-summary-label">方案净现值 · NPV</p>
            <div class="dw-strategy-totals"><div v-for="item in strategyResult.strategies" :key="item.key"><span>{{ item.label }}</span><strong>{{ numberText(item.npv) }} <small>万元</small></strong><span>{{ item.key === 'sell' ? '即期净回收' : '期初现金流' }} {{ signed(item.initialCashFlow) }} 万元</span></div></div>
            <p class="dw-strategy-conclusion">{{ strategyResult.conclusion }}</p>
          </section>
          <div class="dw-strategy-inputs"><section v-for="(fields, key) in strategyFields" :key="key" class="dw-control-group"><h3><span>{{ { hold: '01', renovate: '02', sell: '03' }[key] }}</span>{{ { hold: '继续持有', renovate: '改造后持有', sell: '现在出售' }[key] }}</h3><label v-for="field in fields" :key="field[0]">{{ field[1] }}（{{ field[2] }}）<input v-model.number="state.strategy[key][field[0]]" type="number" :min="field[3]" :max="field[4]" :step="field[5]" :readonly="Boolean(state.strategy.reference) && field[0] === 'annualNetCashFlow'" placeholder="未填" /></label><p v-if="key === 'renovate'" class="dw-footnote">首年经营净现金流应填写未扣停租损失的金额，停租损失单独扣除一次。</p><p v-if="key === 'sell'" class="dw-footnote">净回收 = 出售价格 − 交易费用。此处未扣偿债，避免与资产层持有价值混用。</p></section></div>
          <section class="dw-panel dw-strategy-result"><header class="dw-section-heading"><div><h3>各路径的净现值</h3><p>{{ state.strategy.years || '待填' }} 年 · 同期间 · 同口径 · 单位万元</p></div><span class="dw-outline-label">净现值 · NPV</span></header><EChartPanel :option="strategyChart" :height="220" :empty="!strategyReady" empty-text="请补齐三个方案的全部假设，无费用项目可填写0" aria-label="持有、改造和出售净现值比较" /><details v-if="!strategyReady" class="dw-assumptions"><summary>查看待补齐的假设</summary><ul><li v-for="error in strategyResult.errors" :key="error">{{ error }}</li></ul></details><p class="dw-footnote">项目策略综合考虑未来现金、改造投入和退出价值。调整售价、改造投入或经营预测，可以查看各路径排序如何变化。</p></section>
        </template>
        <div v-else class="dw-strategy-unavailable"><h3>{{ state.strategy.propertyType === 'own' ? '选择是否比较资产出售' : '按合同与经营权，选择项目策略' }}</h3><p>{{ strategyResult.reason || '当前经营方式尚不适用自持资产的持有 / 改造 / 出售比较。' }}</p><p>仍可使用月度预算与经营措施，比较招商、费用和现金改善。</p></div>
      </template>
    </section>
      </fieldset>
    </template>
    <BaseModal :open="leavePrompt.open" title="处理未保存的财务修改" size="small" :layer-index="40" @close="leaveGuard.choose('stay')">
      <p data-testid="planning-leave-message">{{ leavePrompt.label }}前，请选择如何处理当前修改。</p>
      <p v-if="leavePrompt.allowSave">暂存会保留预算、预测、资产策略、合同假设及未保存费用，返回本楼宇、本年度时恢复。仅本次页面有效。</p>
      <p v-else>退出登录会清除本页所有暂存草稿。请先保存需要保留的正式版本，或放弃这些草稿后退出。</p>
      <p v-if="leavePrompt.error" role="alert">{{ leavePrompt.error }}</p>
      <template #footer>
        <button class="btn" :disabled="leavePrompt.resolving" data-testid="planning-leave-stay" @click="leaveGuard.choose('stay')">继续编辑</button>
        <button class="btn" :disabled="leavePrompt.resolving" data-testid="planning-leave-discard" @click="leaveGuard.choose('discard')">放弃修改</button>
        <button v-if="leavePrompt.allowSave" class="btn btn-primary" :disabled="leavePrompt.resolving" data-testid="planning-leave-save" @click="leaveGuard.choose('save')">暂存后继续</button>
      </template>
    </BaseModal>
  </section>
</template>
