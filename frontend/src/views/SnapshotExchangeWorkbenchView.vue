<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, openDownload } from '../lib/api'
import {
  canSubmitPreflight,
  compactExchangeRef,
  formatExchangeTime,
  preflightStatusLabel,
  receiptGuidance,
  receiptStatusLabel,
  uploadErrorsFromFailure
} from '../lib/snapshotExchangeDomain'

const props = defineProps({
  user: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits(['notify'])

const fileInput = ref(null)
const selectedFile = ref(null)
const selectedSourceCode = ref('')
const sources = ref([])
const receipts = ref([])
const receiptTotal = ref(0)
const loading = ref(false)
const preflightBusy = ref(false)
const submitBusy = ref(false)
const dragActive = ref(false)
const pageError = ref('')
const uploadErrors = ref([])
const preflight = ref(null)
const preflightSignature = ref('')
const submittedReceipt = ref(null)
const expandedReceiptNo = ref('')

const capabilities = computed(() => (
  Array.isArray(props.user?.scope?.effective_capabilities)
    ? props.user.scope.effective_capabilities
    : []
))
const canUseWorkbench = computed(() => (
  props.user?.role !== 'viewer' && capabilities.value.includes('data_import_submit')
))
const selectedSource = computed(() => (
  sources.value.find((item) => item.source_system_code === selectedSourceCode.value) || null
))
const currentFileSignature = computed(() => {
  const file = selectedFile.value
  if (!file) return ''
  return [file.name, file.size, file.lastModified].join(':')
})
const preflightCurrent = computed(() => (
  Boolean(preflight.value) && preflightSignature.value === currentFileSignature.value
))
const formalSubmitEnabled = computed(() => (
  preflightCurrent.value
  && canSubmitPreflight(preflight.value)
  && !preflightBusy.value
  && !submitBusy.value
))
const activeResult = computed(() => submittedReceipt.value || preflight.value || null)

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function sourceLabel(source) {
  if (!source) return '未选择来源'
  return `${source.building_name} · ${source.source_system_code}`
}

function statusTone(status) {
  if (status === 'accepted') return 'accepted'
  if (status === 'duplicate') return 'duplicate'
  if (status === 'conflict') return 'conflict'
  return 'rejected'
}

function resetFileResult() {
  preflight.value = null
  preflightSignature.value = ''
  submittedReceipt.value = null
  uploadErrors.value = []
}

function setFile(file) {
  if (!file) return
  selectedFile.value = file
  resetFileResult()
}

function onFileChange(event) {
  setFile(event.target.files?.[0] || null)
}

function openFilePicker() {
  fileInput.value?.click()
}

function onDrop(event) {
  dragActive.value = false
  setFile(event.dataTransfer?.files?.[0] || null)
}

async function loadSources() {
  const payload = await api('/api/snapshot-exchange/v1/uploads/sources', { includeScope: false })
  sources.value = Array.isArray(payload) ? payload : []
  if (!sources.value.some((item) => item.source_system_code === selectedSourceCode.value)) {
    selectedSourceCode.value = sources.value[0]?.source_system_code || ''
  }
}

async function loadReceipts({ announce = false } = {}) {
  const payload = await api('/api/snapshot-exchange/v1/uploads/receipts', { includeScope: false })
  receipts.value = Array.isArray(payload?.items) ? payload.items : []
  receiptTotal.value = Number(payload?.total || 0)
  if (announce) emit('notify', { text: '回执列表已刷新' })
}

async function loadWorkbench() {
  if (!canUseWorkbench.value) return
  loading.value = true
  pageError.value = ''
  try {
    await Promise.all([loadSources(), loadReceipts()])
  } catch (error) {
    pageError.value = error.message || '交换工作台加载失败'
  } finally {
    loading.value = false
  }
}

function downloadTemplate() {
  if (!selectedSourceCode.value) {
    emit('notify', { type: 'error', text: '当前没有可用的登记来源' })
    return
  }
  openDownload(
    `/api/snapshot-exchange/v1/uploads/template.xlsx?source_system_code=${encodeURIComponent(selectedSourceCode.value)}`
  )
}

async function sendFile(path) {
  const form = new FormData()
  form.append('file', selectedFile.value, selectedFile.value.name)
  return api(path, {
    method: 'POST',
    body: form,
    includeScope: false
  })
}

async function runPreflight() {
  if (!selectedFile.value) {
    emit('notify', { type: 'error', text: '请先选择标准 JSON 或 Excel 文件' })
    return
  }
  preflightBusy.value = true
  uploadErrors.value = []
  submittedReceipt.value = null
  try {
    const payload = await sendFile('/api/snapshot-exchange/v1/uploads/preflight')
    preflight.value = payload
    preflightSignature.value = currentFileSignature.value
    uploadErrors.value = Array.isArray(payload?.errors) ? payload.errors : []
    emit('notify', {
      type: payload?.valid ? 'success' : 'warning',
      text: payload?.valid ? '预检通过，可以正式提交' : '预检发现问题，请先修正文件'
    })
  } catch (error) {
    preflight.value = null
    preflightSignature.value = ''
    uploadErrors.value = uploadErrorsFromFailure(error)
    emit('notify', { type: 'error', text: error.message || '文件预检失败' })
  } finally {
    preflightBusy.value = false
  }
}

async function submitFile() {
  if (!formalSubmitEnabled.value) return
  submitBusy.value = true
  uploadErrors.value = []
  try {
    const receipt = await sendFile('/api/snapshot-exchange/v1/uploads/submit')
    submittedReceipt.value = receipt
    uploadErrors.value = Array.isArray(receipt?.errors) ? receipt.errors : []
    emit('notify', {
      type: receipt?.status === 'accepted' ? 'success' : 'warning',
      text: receipt?.status === 'accepted' ? '文件已接收，等待中心复核' : receiptGuidance(receipt)
    })
    await loadReceipts()
  } catch (error) {
    const receipt = error?.payload?.receipt
    if (receipt) submittedReceipt.value = receipt
    uploadErrors.value = uploadErrorsFromFailure(error)
    emit('notify', { type: 'error', text: error.message || '正式提交失败' })
    await loadReceipts().catch(() => {})
  } finally {
    submitBusy.value = false
  }
}

function toggleReceipt(receiptNo) {
  expandedReceiptNo.value = expandedReceiptNo.value === receiptNo ? '' : receiptNo
}

onMounted(loadWorkbench)
</script>

<template>
  <section class="content exchange-page" data-testid="snapshot-exchange-workbench">
    <article v-if="!canUseWorkbench" class="card exchange-denied">
      <span>ACCESS CLOSED</span>
      <h2>{{ $t("当前账号没有标准文件提交权限") }}</h2>
      <p>{{ $t("请联系管理员开通快照提交权限。") }}</p>
    </article>

    <template v-else>
      <header class="exchange-masthead">
        <div>
          <span class="exchange-kicker">OPS EXCHANGE · INTAKE DESK / V1</span>
          <h2>{{ $t("标准文件接收工作台") }}</h2>
          <p>{{ $t("上传 JSON 或 Excel 文件，检查通过后提交复核。") }}</p>
        </div>
        <aside>
          <span>DATA CLASS</span>
          <strong>SIMULATED</strong>
          <small>{{ $t("当前全部为模拟数据") }}</small>
        </aside>
      </header>

      <div class="exchange-route" :aria-label="$t(&quot;文件处理流程&quot;)">
        <div class="active"><i>01</i><span>{{ $t("选择标准包") }}</span><small>JSON / XLSX</small></div>
        <div :class="{ active: preflightCurrent }"><i>02</i><span>{{ $t("文件检查") }}</span><small>{{ $t("检查文件内容") }}</small></div>
        <div :class="{ active: submittedReceipt }"><i>03</i><span>{{ $t("正式接收") }}</span><small>{{ $t("生成稳定回执") }}</small></div>
        <div><i>04</i><span>{{ $t("中心复核") }}</span><small>{{ $t("通过后发布") }}</small></div>
      </div>

      <article v-if="pageError" class="card exchange-page-error">
        <div><strong>{{ $t("工作台暂不可用") }}</strong><span>{{ $t(pageError) }}</span></div>
        <button class="btn" type="button" @click="loadWorkbench">{{ $t("重试") }}</button>
      </article>

      <div class="exchange-grid">
        <article class="card exchange-intake">
          <div class="exchange-section-heading">
            <div><span>INTAKE / 01</span><h3>{{ $t("文件与登记来源") }}</h3></div>
            <button class="btn btn-compact" type="button" :disabled="!selectedSourceCode" data-testid="exchange-template-download" @click="downloadTemplate">{{ $t(" 下载 Excel 模板 ") }}</button>
          </div>

          <label class="exchange-source-field">
            <span>{{ $t("登记来源") }}</span>
            <select v-model="selectedSourceCode" class="select" :disabled="loading || !sources.length" data-testid="exchange-source-select">
              <option v-if="!sources.length" value="">{{ $t("暂无获授权来源") }}</option>
              <option v-for="source in sources" :key="source.source_system_code" :value="source.source_system_code">
                {{ $t(sourceLabel(source)) }}
              </option>
            </select>
            <small>{{ $t("只显示当前账号可编辑楼宇的公开来源代码，不使用内部编号。") }}</small>
          </label>

          <div
            class="exchange-dropzone"
            :class="{ dragging: dragActive, selected: selectedFile }"
            role="button"
            tabindex="0"
            @click="openFilePicker"
            @keydown.enter.prevent="openFilePicker"
            @keydown.space.prevent="openFilePicker"
            @dragenter.prevent="dragActive = true"
            @dragover.prevent="dragActive = true"
            @dragleave.prevent="dragActive = false"
            @drop.prevent="onDrop"
          >
            <input
              ref="fileInput"
              data-testid="exchange-file-input"
              type="file"
              accept=".json,.xlsx,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              @change="onFileChange"
            />
            <b>{{ $t(selectedFile ? 'FILE LOCKED' : 'DROP STANDARD PACKAGE') }}</b>
            <strong>{{ selectedFile?.name || '将 JSON / Excel 拖到这里，或点击选择' }}</strong>
            <small v-if="selectedFile">{{ $t(formatBytes(selectedFile.size)) }}{{ $t(" · 已选择") }}</small>
            <small v-else>{{ $t("支持 JSON、Excel（.xlsx）") }}</small>
          </div>

          <div class="exchange-actions">
            <button class="btn" type="button" :disabled="!selectedFile || preflightBusy || submitBusy" data-testid="exchange-preflight" @click="runPreflight">
              {{ $t(preflightBusy ? '文件检查中…' : '运行预检') }}
            </button>
            <button class="btn btn-primary" type="button" :disabled="!formalSubmitEnabled" data-testid="exchange-submit" @click="submitFile">
              {{ $t(submitBusy ? '正式提交中…' : '提交复核') }}
            </button>
          </div>
          <p class="exchange-retry-note">{{ $t(" 提交中断时，使用原文件和原投递号重试。 ") }}</p>
        </article>

        <article class="card exchange-inspection">
          <div class="exchange-section-heading">
            <div><span>INSPECTION / 02</span><h3>{{ $t("预检与提交结果") }}</h3></div>
            <span v-if="activeResult?.would_status || activeResult?.status" class="exchange-status" :class="statusTone(activeResult.status || activeResult.would_status)">
              {{ $t(activeResult.status ? receiptStatusLabel(activeResult.status) : preflightStatusLabel(activeResult.would_status)) }}
            </span>
          </div>

          <div v-if="!activeResult && !uploadErrors.length" class="exchange-empty-result">
            <i>NO RESULT</i>
            <strong>{{ $t("尚未运行预检") }}</strong>
            <span>{{ $t("选择文件后点击“运行预检”。") }}</span>
          </div>

          <div v-else data-testid="exchange-result">
            <dl class="exchange-result-grid">
              <div><dt>{{ $t("文件格式") }}</dt><dd>{{ $t(activeResult.file_format?.toUpperCase() || '--') }}</dd></div>
              <div><dt>{{ $t("投递号") }}</dt><dd :title="$t(activeResult.delivery_id)">{{ $t(compactExchangeRef(activeResult.delivery_id, 16, 8)) }}</dd></div>
              <div><dt>{{ $t("来源") }}</dt><dd>{{ $t(activeResult.source_system_code || '--') }}</dd></div>
              <div><dt>{{ $t("楼宇") }}</dt><dd>{{ $t(activeResult.building_code || '--') }}</dd></div>
              <div class="digest"><dt>{{ $t("中心摘要") }}</dt><dd :title="$t(activeResult.server_content_digest)">{{ $t(compactExchangeRef(activeResult.server_content_digest, 12, 10)) }}</dd></div>
              <div><dt>{{ $t("公开回执") }}</dt><dd :title="$t(activeResult.receipt_no)">{{ $t(compactExchangeRef(activeResult.receipt_no, 12, 8)) }}</dd></div>
            </dl>

            <div v-if="submittedReceipt" class="exchange-guidance" :class="statusTone(submittedReceipt.status)" data-testid="exchange-submitted-guidance">
              <strong>{{ $t(receiptStatusLabel(submittedReceipt.status)) }}</strong>
              <span>{{ $t(receiptGuidance(submittedReceipt)) }}</span>
            </div>

            <ol v-if="uploadErrors.length" class="exchange-errors">
              <li v-for="(issue, index) in uploadErrors" :key="`${issue.code}-${issue.field}-${issue.row}-${index}`">
                <code>{{ $t(issue.code) }}</code>
                <strong>{{ $t(issue.message) }}</strong>
                <small v-if="issue.field || issue.row">{{ $t(issue.field || '文件') }}{{ $t(issue.row ? ` · 第 ${issue.row} 行` : '') }}</small>
              </li>
            </ol>
          </div>
        </article>
      </div>

      <article class="card exchange-ledger">
        <div class="exchange-section-heading ledger-heading">
          <div>
            <span>RECEIPT LEDGER / 03</span>
            <h3>{{ $t("我的上传回执") }}</h3>
            <p>{{ $t("查看我的提交记录。") }}</p>
          </div>
          <div class="ledger-actions">
            <b>{{ $t(receiptTotal) }}</b>
            <button class="btn btn-compact" type="button" :disabled="loading" @click="loadReceipts({ announce: true })">{{ $t("刷新") }}</button>
          </div>
        </div>

        <div v-if="loading && !receipts.length" class="exchange-ledger-empty">{{ $t("正在读取回执账本…") }}</div>
        <div v-else-if="!receipts.length" class="exchange-ledger-empty">{{ $t("暂无提交记录。") }}</div>
        <div v-else class="exchange-table-wrap" data-testid="exchange-receipt-ledger">
          <table class="exchange-table">
            <thead><tr><th>{{ $t("状态") }}</th><th>{{ $t("投递 / 回执") }}</th><th>{{ $t("来源 / 楼宇") }}</th><th>{{ $t("中心摘要") }}</th><th>{{ $t("首次接收") }}</th><th></th></tr></thead>
            <tbody v-for="receipt in receipts" :key="receipt.receipt_no">
              <tr>
                <td data-label="状态"><span class="exchange-status" :class="statusTone(receipt.status)">{{ $t(receiptStatusLabel(receipt.status)) }}</span></td>
                <td data-label="投递 / 回执"><strong>{{ $t(compactExchangeRef(receipt.delivery_id, 18, 7)) }}</strong><small>{{ $t(compactExchangeRef(receipt.receipt_no, 12, 7)) }}</small></td>
                <td data-label="来源 / 楼宇"><strong>{{ $t(receipt.source_system_code) }}</strong><small>{{ $t(receipt.building_code) }}</small></td>
                <td data-label="中心摘要"><code :title="$t(receipt.server_content_digest)">{{ $t(compactExchangeRef(receipt.server_content_digest, 10, 8)) }}</code></td>
                <td data-label="首次接收"><span>{{ $t(formatExchangeTime(receipt.first_received_at)) }}</span><small>{{ $t(receipt.center_status === 'pending_review' ? '待中心复核' : '未形成候选') }}</small></td>
                <td data-label="操作"><button class="btn btn-compact" type="button" @click="toggleReceipt(receipt.receipt_no)">{{ $t(expandedReceiptNo === receipt.receipt_no ? '收起' : '详情') }}</button></td>
              </tr>
              <tr v-if="expandedReceiptNo === receipt.receipt_no" class="exchange-detail-row">
                <td colspan="6">
                  <div class="exchange-receipt-detail">
                    <div><span>{{ $t("来源发布修订") }}</span><strong>{{ $t(receipt.source_publication_revision) }}</strong></div>
                    <div><span>{{ $t("原回执") }}</span><strong>{{ $t(receipt.original_receipt_no || '--') }}</strong></div>
                    <div><span>{{ $t("更正引用") }}</span><strong>{{ $t(receipt.correction_of_receipt_no || '--') }}</strong></div>
                    <div><span>{{ $t("中心候选") }}</span><strong>{{ $t(receipt.candidate_batch_ref || '--') }}</strong></div>
                    <p>{{ $t(receiptGuidance(receipt)) }}</p>
                    <ol v-if="receipt.errors?.length" class="exchange-errors compact">
                      <li v-for="(issue, index) in receipt.errors" :key="`${receipt.receipt_no}-${index}`">
                        <code>{{ $t(issue.code) }}</code><strong>{{ $t(issue.message) }}</strong><small>{{ $t(issue.field || '文件') }}{{ $t(issue.row ? ` · 第 ${issue.row} 行` : '') }}</small>
                      </li>
                    </ol>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>
    </template>
  </section>
</template>

<style scoped>
.exchange-page {
  --exchange-ink: #14211f;
  --exchange-accent: #b7e449;
  --exchange-cyan: #5ad6c7;
  display: grid;
  gap: 18px;
}

.exchange-masthead {
  position: relative;
  overflow: hidden;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 28px;
  min-height: 190px;
  padding: 30px 34px;
  color: #f5fff9;
  border-radius: 18px;
  background:
    linear-gradient(115deg, rgba(90, 214, 199, .16), transparent 42%),
    repeating-linear-gradient(135deg, transparent 0 34px, rgba(255,255,255,.035) 34px 35px),
    var(--exchange-ink);
  box-shadow: 0 20px 44px rgba(20, 33, 31, .18);
}

.exchange-masthead::after {
  content: '';
  position: absolute;
  inset: auto -70px -105px auto;
  width: 280px;
  height: 280px;
  border: 44px solid rgba(183, 228, 73, .15);
  border-radius: 50%;
}

.exchange-kicker,
.exchange-section-heading span,
.exchange-route i,
.exchange-dropzone b,
.exchange-masthead aside span,
.exchange-empty-result i {
  font-family: 'Cascadia Mono', 'SFMono-Regular', Consolas, monospace;
  letter-spacing: .08em;
}

.exchange-kicker {
  display: block;
  margin-bottom: 24px;
  color: var(--exchange-cyan);
  font-size: 12px;
  font-weight: 800;
}

.exchange-masthead h2 {
  margin: 0;
  max-width: 720px;
  color: #fff;
  font-size: clamp(28px, 4vw, 46px);
  line-height: 1.08;
  letter-spacing: -.04em;
}

.exchange-masthead p {
  max-width: 760px;
  margin: 18px 0 0;
  color: rgba(245,255,249,.72);
  font-size: 15px;
  line-height: 1.75;
}

.exchange-masthead aside {
  position: relative;
  z-index: 1;
  align-self: center;
  min-width: 160px;
  padding: 18px 20px;
  border: 1px solid rgba(183,228,73,.55);
  background: rgba(0,0,0,.18);
}

.exchange-masthead aside span,
.exchange-masthead aside small { display: block; color: rgba(245,255,249,.58); font-size: 10px; }
.exchange-masthead aside strong { display: block; margin: 8px 0; color: var(--exchange-accent); font-size: 22px; letter-spacing: .08em; }

.exchange-route {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  background: var(--surface);
}

.exchange-route > div { position: relative; display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; padding: 16px 18px; border-right: 1px solid var(--border); }
.exchange-route > div:last-child { border-right: 0; }
.exchange-route i { grid-row: 1 / 3; align-self: center; color: var(--text-faint); font-size: 12px; font-style: normal; }
.exchange-route span { color: var(--text-strong); font-weight: 800; }
.exchange-route small { color: var(--text-faint); }
.exchange-route .active { background: linear-gradient(90deg, rgba(90,214,199,.12), transparent); box-shadow: inset 0 -3px var(--exchange-cyan); }
.exchange-route .active i { color: #12988b; }

.exchange-grid { display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(360px, .92fr); gap: 18px; }
.exchange-intake, .exchange-inspection, .exchange-ledger { padding: 0; overflow: hidden; }
.exchange-section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 20px 22px; border-bottom: 1px solid var(--border); }
.exchange-section-heading span { display: block; color: #168f83; font-size: 10px; font-weight: 800; }
.exchange-section-heading h3 { margin: 4px 0 0; color: var(--text-strong); font-size: 18px; }
.exchange-section-heading p { margin: 6px 0 0; color: var(--text-muted); }

.exchange-source-field { display: grid; gap: 7px; padding: 20px 22px 0; }
.exchange-source-field > span { color: var(--text-strong); font-size: 12px; font-weight: 800; }
.exchange-source-field small { color: var(--text-faint); line-height: 1.5; }

.exchange-dropzone { display: grid; place-items: center; min-height: 200px; margin: 20px 22px; padding: 28px; text-align: center; border: 1px dashed var(--border-strong); background: repeating-linear-gradient(135deg, var(--surface-muted) 0 10px, transparent 10px 20px); cursor: pointer; transition: .18s ease; }
.exchange-dropzone:hover, .exchange-dropzone.dragging { border-color: #159d90; box-shadow: inset 0 0 0 3px rgba(90,214,199,.14); transform: translateY(-2px); }
.exchange-dropzone.selected { border-style: solid; background: linear-gradient(135deg, rgba(90,214,199,.12), var(--surface)); }
.exchange-dropzone input { display: none; }
.exchange-dropzone b { color: #168f83; font-size: 10px; }
.exchange-dropzone strong { max-width: 520px; margin: 12px 0 8px; color: var(--text-strong); font-size: 16px; overflow-wrap: anywhere; }
.exchange-dropzone small { color: var(--text-muted); line-height: 1.5; }

.exchange-actions { display: flex; justify-content: flex-end; gap: 10px; padding: 0 22px 16px; }
.exchange-retry-note { margin: 0; padding: 14px 22px; color: #6b5b18; border-top: 1px solid #eadc97; background: #fff9dc; font-size: 12px; line-height: 1.6; }

.exchange-empty-result { min-height: 350px; display: grid; place-content: center; justify-items: center; padding: 28px; text-align: center; }
.exchange-empty-result i { color: var(--text-faint); font-size: 11px; font-style: normal; }
.exchange-empty-result strong { margin: 12px 0 8px; color: var(--text-strong); font-size: 18px; }
.exchange-empty-result span { max-width: 340px; color: var(--text-muted); line-height: 1.6; }

.exchange-result-grid { display: grid; grid-template-columns: 1fr 1fr; margin: 0; }
.exchange-result-grid > div { min-width: 0; padding: 18px 20px; border-bottom: 1px solid var(--border); border-right: 1px solid var(--border); }
.exchange-result-grid > div:nth-child(2n) { border-right: 0; }
.exchange-result-grid dt { color: var(--text-faint); font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }
.exchange-result-grid dd { margin: 7px 0 0; color: var(--text-strong); font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; font-weight: 700; overflow-wrap: anywhere; }

.exchange-status { display: inline-flex; align-items: center; min-height: 25px; padding: 3px 9px; border-radius: 99px; font-size: 11px; font-weight: 800; white-space: nowrap; }
.exchange-status.accepted { color: #087568; background: #d7f7ef; }
.exchange-status.duplicate { color: #245db5; background: #e7f0ff; }
.exchange-status.rejected { color: #a23a2c; background: #ffe9e5; }
.exchange-status.conflict { color: #8b5b04; background: #fff1c7; }

.exchange-guidance { display: grid; gap: 5px; margin: 18px 20px; padding: 14px 16px; border-left: 4px solid; background: var(--surface-muted); }
.exchange-guidance.accepted { border-color: #19a38f; }
.exchange-guidance.duplicate { border-color: #3b82f6; }
.exchange-guidance.rejected { border-color: #e25b47; }
.exchange-guidance.conflict { border-color: #d69d27; }
.exchange-guidance strong { color: var(--text-strong); }
.exchange-guidance span { color: var(--text-muted); line-height: 1.55; }

.exchange-errors { display: grid; gap: 8px; margin: 18px 20px 22px; padding: 0; list-style: none; }
.exchange-errors li { display: grid; grid-template-columns: auto 1fr; gap: 3px 10px; padding: 12px 14px; border: 1px solid #f2c9c2; background: #fff5f3; }
.exchange-errors code { grid-row: 1 / 3; align-self: start; color: #a23a2c; font-size: 10px; }
.exchange-errors strong { color: #7d2d22; font-size: 12px; }
.exchange-errors small { color: #a25b51; }
.exchange-errors.compact { margin: 12px 0 0; }

.ledger-heading { align-items: end; }
.ledger-actions { display: flex; align-items: center; gap: 12px; }
.ledger-actions b { color: var(--text-strong); font-size: 22px; }
.exchange-ledger-empty { padding: 42px 22px; color: var(--text-muted); text-align: center; }
.exchange-table-wrap { overflow-x: auto; }
.exchange-table { width: 100%; border-collapse: collapse; min-width: 920px; }
.exchange-table th { padding: 11px 14px; color: var(--text-faint); background: var(--surface-muted); border-bottom: 1px solid var(--border); font-size: 10px; letter-spacing: .06em; text-align: left; }
.exchange-table td { padding: 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }
.exchange-table td strong, .exchange-table td small { display: block; }
.exchange-table td strong { color: var(--text-strong); font-size: 12px; }
.exchange-table td small { margin-top: 4px; color: var(--text-faint); }
.exchange-table td code { color: #168f83; }
.exchange-detail-row td { padding: 0; background: var(--surface-muted); }
.exchange-receipt-detail { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; padding: 18px 20px 22px; border-left: 4px solid var(--exchange-cyan); }
.exchange-receipt-detail > div { min-width: 0; }
.exchange-receipt-detail span, .exchange-receipt-detail strong { display: block; }
.exchange-receipt-detail span { color: var(--text-faint); font-size: 10px; }
.exchange-receipt-detail strong { margin-top: 5px; color: var(--text-strong); font-size: 11px; overflow-wrap: anywhere; }
.exchange-receipt-detail > p, .exchange-receipt-detail > ol { grid-column: 1 / -1; }
.exchange-receipt-detail > p { margin: 0; padding-top: 10px; color: var(--text-muted); border-top: 1px solid var(--border); }

.exchange-page-error, .exchange-denied { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 22px; }
.exchange-page-error div { display: grid; gap: 5px; }
.exchange-page-error span, .exchange-denied p { color: var(--text-muted); }
.exchange-denied { display: block; border-left: 5px solid #e25b47; }
.exchange-denied > span { color: #b94736; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px; }
.exchange-denied h2 { color: var(--text-strong); }

@media (max-width: 980px) {
  .exchange-grid { grid-template-columns: 1fr; }
  .exchange-route { grid-template-columns: 1fr 1fr; }
  .exchange-route > div:nth-child(2) { border-right: 0; }
  .exchange-route > div:nth-child(-n+2) { border-bottom: 1px solid var(--border); }
}

@media (max-width: 680px) {
  .exchange-masthead { grid-template-columns: 1fr; padding: 24px; }
  .exchange-masthead aside { width: fit-content; }
  .exchange-route { grid-template-columns: 1fr; }
  .exchange-route > div { border-right: 0; border-bottom: 1px solid var(--border); }
  .exchange-route > div:last-child { border-bottom: 0; }
  .exchange-section-heading, .exchange-actions { align-items: stretch; flex-direction: column; }
  .exchange-actions .btn { width: 100%; }
  .exchange-result-grid { grid-template-columns: 1fr; }
  .exchange-result-grid > div { border-right: 0; }
  .exchange-receipt-detail { grid-template-columns: 1fr 1fr; }
  .exchange-table-wrap { overflow: visible; }
  .exchange-table { display: block; min-width: 0; }
  .exchange-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .exchange-table tbody { display: block; }
  .exchange-table tbody > tr:not(.exchange-detail-row) { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px 14px; padding: 16px 18px; border-bottom: 1px solid var(--border); }
  .exchange-table tbody > tr:not(.exchange-detail-row) > td { min-width: 0; padding: 0; border-bottom: 0; }
  .exchange-table tbody > tr:not(.exchange-detail-row) > td:nth-child(1) { grid-column: 1; }
  .exchange-table tbody > tr:not(.exchange-detail-row) > td:nth-child(6) { grid-column: 2; grid-row: 1; justify-self: end; }
  .exchange-table tbody > tr:not(.exchange-detail-row) > td:nth-child(n+2):nth-child(-n+5) { grid-column: 1 / -1; display: grid; grid-template-columns: 86px minmax(0, 1fr); gap: 3px 10px; padding-top: 10px; border-top: 1px solid var(--border); }
  .exchange-table tbody > tr:not(.exchange-detail-row) > td:nth-child(n+2):nth-child(-n+5)::before { content: attr(data-label); grid-row: 1 / 3; color: var(--text-faint); font-size: 10px; font-weight: 700; }
  .exchange-table tbody > tr:not(.exchange-detail-row) > td:nth-child(n+2):nth-child(-n+5) > * { grid-column: 2; overflow-wrap: anywhere; }
  .exchange-detail-row { display: block; }
  .exchange-detail-row td { display: block; }
}
</style>
