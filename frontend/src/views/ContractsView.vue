<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, openDownload } from '../lib/api'
import { formatArea, formatCurrencyFixed } from '../lib/format'
import StatusBadge from '../components/StatusBadge.vue'
import ContractFormModal from '../components/ContractFormModal.vue'
import ContractDetailModal from '../components/ContractDetailModal.vue'
import PdfUploadModal from '../components/PdfUploadModal.vue'

const props = defineProps({
  user: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['notify', 'refresh-dashboard'])
const canEdit = computed(() => ['admin', 'editor'].includes(props.user?.role))

const state = reactive({
  loading: false,
  list: [],
  total: 0,
  page: 1,
  pageSize: 20,
  keyword: '',
  tower: '',
  status: '',
  selected: []
})

const contractSort = reactive({
  key: '',
  direction: ''
})

const formOpen = ref(false)
const detailOpen = ref(false)
const pdfOpen = ref(false)
const editing = ref(null)
const detail = ref(null)
const detailLogs = ref([])
const detailPlan = ref([])
const detailSummary = ref({})
const pdfContract = ref(null)
const importInput = ref(null)

async function fetchContracts() {
  state.loading = true
  try {
    const params = new URLSearchParams({
      page: String(state.page),
      page_size: String(state.pageSize)
    })
    if (state.keyword) params.set('keyword', state.keyword)
    if (state.tower) params.set('tower', state.tower)
    if (state.status) params.set('status', state.status)
    if (contractSort.key) params.set('sort_by', contractSort.key)
    if (contractSort.direction) params.set('sort_dir', contractSort.direction)

    const data = await api(`/api/contracts?${params}`)
    state.list = data.items
    state.total = data.total
    state.selected = []
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    state.loading = false
  }
}

async function openCreate() {
  editing.value = null
  if (canEdit.value) {
    try {
      const next = await api('/api/contracts/next-no')
      editing.value = { contract_no: next.next_no }
    } catch {
      editing.value = null
    }
  }
  formOpen.value = true
}

async function saveContract(payload) {
  try {
    if (payload.id) {
      await api(`/api/contracts/${payload.id}`, { method: 'PUT', body: JSON.stringify(payload) })
      emit('notify', { text: '合同更新成功' })
    } else {
      await api('/api/contracts', { method: 'POST', body: JSON.stringify(payload) })
      emit('notify', { text: '合同创建成功' })
    }
    formOpen.value = false
    await fetchContracts()
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function openDetail(row) {
  detail.value = await api(`/api/contracts/${row.id}`)
  const plan = await api(`/api/contracts/${row.id}/rent-plan`)
  detailPlan.value = plan.plan || []
  detailSummary.value = plan.summary || {}
  detailLogs.value = canEdit.value ? await api(`/api/contracts/${row.id}/logs`) : []
  detailOpen.value = true
}

async function removeContract(row) {
  if (!window.confirm(`确定删除 ${row.contract_no} 吗？`)) return
  try {
    await api(`/api/contracts/${row.id}`, { method: 'DELETE' })
    emit('notify', { text: '合同已删除' })
    await fetchContracts()
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function batchDelete() {
  if (!state.selected.length || !window.confirm(`确定删除选中的 ${state.selected.length} 条合同吗？`)) return
  try {
    await api('/api/contracts/batch-delete', { method: 'POST', body: JSON.stringify(state.selected) })
    emit('notify', { text: '批量删除成功' })
    await fetchContracts()
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function importExcel(event) {
  const file = event.target.files?.[0]
  if (!file) return

  const payload = new FormData()
  payload.append('file', file)

  try {
    const result = await api('/api/contracts/import', { method: 'POST', body: payload })
    emit('notify', { text: `导入完成：${result.imported} 条，跳过 ${result.skipped} 条` })
    await fetchContracts()
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    event.target.value = ''
  }
}

async function uploadPdf(file) {
  if (!pdfContract.value) return

  const payload = new FormData()
  payload.append('file', file)

  try {
    await api(`/api/contracts/${pdfContract.value.id}/upload-pdf`, { method: 'POST', body: payload })
    emit('notify', { text: 'PDF 上传成功' })
    pdfOpen.value = false
    await fetchContracts()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

function exportExcel() {
  const params = new URLSearchParams()
  if (state.keyword) params.set('keyword', state.keyword)
  if (state.tower) params.set('tower', state.tower)
  if (state.status) params.set('status', state.status)
  openDownload(`/api/contracts/export/excel?${params}`)
}

function triggerImport() {
  importInput.value?.click()
}

function toggleContractSort(key) {
  if (contractSort.key !== key) {
    contractSort.key = key
    contractSort.direction = 'asc'
    state.page = 1
    fetchContracts()
    return
  }

  if (contractSort.direction === 'asc') {
    contractSort.direction = 'desc'
    state.page = 1
    fetchContracts()
    return
  }

  if (contractSort.direction === 'desc') {
    contractSort.key = ''
    contractSort.direction = ''
    state.page = 1
    fetchContracts()
    return
  }

  contractSort.direction = 'asc'
  state.page = 1
  fetchContracts()
}

function contractSortIndicator(key) {
  if (contractSort.key !== key) return '↕'
  return contractSort.direction === 'asc' ? '↑' : '↓'
}

onMounted(fetchContracts)
</script>

<template>
  <section class="content">
    <article class="card table-card contracts-shell">
      <div class="card-header page-block">
        <div>
          <div class="section-label">合同台账</div>
          <h3 class="card-title">合同管理</h3>
          <p class="helper-text">集中处理筛选查询、导入导出、详情查看和 PDF 附件管理。</p>
        </div>
      </div>

      <div class="page-block contract-toolbar-shell contract-toolbar-shell-contracts">
        <div class="filter-cluster contract-filter-cluster">
          <input
            v-model="state.keyword"
            class="input"
            placeholder="合同编号 / 室号 / 客户"
            data-testid="contracts-search-input"
            @keyup.enter="fetchContracts"
          />
          <button class="btn btn-primary contract-search-btn" data-testid="contracts-search-button" @click="fetchContracts">查询</button>
          <input
            v-model="state.tower"
            class="input"
            placeholder="楼栋/区域"
            data-testid="contracts-tower-filter"
            @keyup.enter="fetchContracts"
          />
          <select v-model="state.status" class="select" data-testid="contracts-status-filter" @change="fetchContracts">
            <option value="">全部状态</option>
            <option value="在租">在租</option>
            <option value="即将到期">即将到期</option>
            <option value="已到期">已到期</option>
            <option value="已续租">已续租</option>
            <option value="已退租">已退租</option>
            <option value="逾期占用">逾期占用</option>
            <option value="待生效">待生效</option>
          </select>
        </div>

        <div class="action-cluster">
          <button v-if="canEdit && state.selected.length" class="btn btn-danger" @click="batchDelete">
            删除选中 {{ state.selected.length }}
          </button>
          <button v-if="canEdit" class="btn" @click="openDownload('/api/contracts/export/template')">下载模板</button>
          <button v-if="canEdit" class="btn" @click="triggerImport">导入 Excel</button>
          <input ref="importInput" class="visually-hidden" type="file" accept=".xlsx,.xls" @change="importExcel" />
          <button v-if="canEdit" class="btn" @click="exportExcel">导出数据</button>
          <button v-if="canEdit" class="btn btn-primary" @click="openCreate">新建合同</button>
        </div>
      </div>

      <div class="table-wrapper">
        <table class="data-table contract-ledger-table" data-testid="contracts-table">
          <thead>
            <tr>
              <th v-if="canEdit" class="check-cell">
                <input
                  type="checkbox"
                  :checked="state.list.length > 0 && state.selected.length === state.list.length"
                  @change="state.selected = $event.target.checked ? state.list.map((item) => item.id) : []"
                />
              </th>
              <th>
                <button class="table-sort-btn" data-testid="contracts-sort-contract-no" :class="{ active: contractSort.key === 'contract_no' }" @click="toggleContractSort('contract_no')">
                  合同编号 {{ contractSortIndicator('contract_no') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn" :class="{ active: contractSort.key === 'tower' }" @click="toggleContractSort('tower')">
                  楼栋/区域 {{ contractSortIndicator('tower') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn" :class="{ active: contractSort.key === 'room_no' }" @click="toggleContractSort('room_no')">
                  室号 {{ contractSortIndicator('room_no') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn" :class="{ active: contractSort.key === 'tenant_name' }" @click="toggleContractSort('tenant_name')">
                  客户 {{ contractSortIndicator('tenant_name') }}
                </button>
              </th>
              <th class="right">
                <button class="table-sort-btn right" :class="{ active: contractSort.key === 'area' }" @click="toggleContractSort('area')">
                  面积 {{ contractSortIndicator('area') }}
                </button>
              </th>
              <th class="right">
                <button class="table-sort-btn right" data-testid="contracts-sort-monthly-rent" :class="{ active: contractSort.key === 'monthly_rent' }" @click="toggleContractSort('monthly_rent')">
                  月租金 {{ contractSortIndicator('monthly_rent') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn" :class="{ active: contractSort.key === 'status' }" @click="toggleContractSort('status')">
                  状态 {{ contractSortIndicator('status') }}
                </button>
              </th>
              <th class="actions-cell">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in state.list" :key="row.id">
              <td v-if="canEdit" class="check-cell">
                <input v-model="state.selected" type="checkbox" :value="row.id" />
              </td>
              <td>{{ row.contract_no }}</td>
              <td>{{ row.tower }}</td>
              <td>{{ row.room_no }}</td>
              <td>{{ row.tenant_name }}</td>
              <td class="right">{{ formatArea(row.area) }}</td>
              <td class="right">{{ formatCurrencyFixed(row.monthly_rent) }}</td>
              <td><StatusBadge :status="row.status" /></td>
              <td class="actions-cell">
                <button class="icon-btn" title="查看详情" @click="openDetail(row)">看</button>
                <button v-if="canEdit" class="icon-btn" title="编辑合同" @click="editing = row; formOpen = true">改</button>
                <button v-if="canEdit" class="icon-btn" title="上传 PDF" @click="pdfContract = row; pdfOpen = true">PDF</button>
                <button v-if="canEdit" class="icon-btn danger" title="删除合同" @click="removeContract(row)">删</button>
              </td>
            </tr>
            <tr v-if="!state.list.length && !state.loading">
              <td :colspan="canEdit ? 9 : 8" class="list-empty">暂无合同数据</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="page-block table-footer">
        <span class="helper-text">共 {{ state.total }} 条</span>
        <div class="spacer" />
        <button class="btn" :disabled="state.page <= 1" @click="state.page -= 1; fetchContracts()">上一页</button>
        <span>{{ state.page }} / {{ Math.max(1, Math.ceil(state.total / state.pageSize)) }}</span>
        <button
          class="btn"
          :disabled="state.page >= Math.ceil(state.total / state.pageSize)"
          @click="state.page += 1; fetchContracts()"
        >
          下一页
        </button>
      </div>
    </article>

    <ContractFormModal :open="formOpen" :contract="editing" :can-edit="canEdit" @close="formOpen = false" @save="saveContract" />
    <ContractDetailModal
      :open="detailOpen"
      :contract="detail"
      :logs="detailLogs"
      :rent-plan="detailPlan"
      :rent-summary="detailSummary"
      :show-logs="canEdit"
      @close="detailOpen = false"
    />
    <PdfUploadModal
      :open="pdfOpen"
      :contract="pdfContract"
      @close="pdfOpen = false"
      @upload="uploadPdf"
      @view="pdfContract && openDownload(`/api/contracts/${pdfContract.id}/pdf`)"
    />
  </section>
</template>
