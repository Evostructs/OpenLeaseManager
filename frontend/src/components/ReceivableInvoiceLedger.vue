<template>
  <div>
    <div class="workbench-header">
      <div>
        <h4>{{ $t("开票台账") }}</h4>
      </div>
      <button v-if="rows.length" class="btn" @click="emit('export')">{{ $t("导出发票台账") }}</button>
    </div>
    <div class="toolbar tool-toolbar compact-ledger-toolbar">
      <select v-model="filters.invoice_status" class="select">
        <option value="">{{ $t("全部发票状态") }}</option>
        <option value="未开票">{{ $t("未开票") }}</option>
        <option value="待开票">{{ $t("待开票") }}</option>
        <option value="已开票">{{ $t("已开票") }}</option>
        <option value="无需开票">{{ $t("无需开票") }}</option>
        <option value="红冲/作废">{{ $t("红冲/作废") }}</option>
      </select>
      <input v-model="filters.keyword" class="input ledger-keyword-input" :placeholder="$t(&quot;合同号 / 客户 / 室号 / 发票号&quot;)" @keyup.enter="emit('refresh')" />
      <button class="btn btn-primary" @click="emit('refresh')">{{ $t("刷新发票台账") }}</button>
    </div>
    <div v-if="ledger" class="tool-summary-grid compact-stats ledger-summary-grid">
      <div class="tool-summary-card">
        <span>{{ $t("台账记录") }}</span>
        <strong>{{ $t(ledger.count || 0) }}</strong>
      </div>
      <div class="tool-summary-card">
        <span>{{ $t("应收租金") }}</span>
        <strong>{{ $t(formatCurrencyFixed(ledger.total_rent)) }}</strong>
      </div>
      <div class="tool-summary-card">
        <span>{{ $t("本月未收") }}</span>
        <strong>{{ $t(formatCurrencyFixed(ledger.total_balance)) }}</strong>
      </div>
      <div class="tool-summary-card">
        <span>{{ $t("开票金额") }}</span>
        <strong>{{ $t(formatCurrencyFixed(ledger.total_invoice_amount)) }}</strong>
      </div>
    </div>
    <div v-if="rows.length" class="table-wrapper compact-table">
      <table class="data-table ledger-table invoice-ledger-table">
        <thead>
          <tr>
            <th>{{ $t("合同 / 客户") }}</th>
            <th>{{ $t("房源") }}</th>
            <th class="right">{{ $t("应收") }}</th>
            <th class="right">{{ $t("已收") }}</th>
            <th class="right">{{ $t("余额") }}</th>
            <th>{{ $t("发票状态") }}</th>
            <th>{{ $t("发票号") }}</th>
            <th>{{ $t("开票日期") }}</th>
            <th class="right">{{ $t("开票金额") }}</th>
            <th>{{ $t("备注") }}</th>
            <th>{{ $t("操作") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in paginatedRows" :key="`invoice-${row.contract_id}`">
            <td>
              <div class="cell-stack">
                <strong>{{ row.contract_no }}</strong>
                <span>{{ row.tenant_name }}</span>
              </div>
            </td>
            <td>
              <div class="cell-stack compact">
                <strong>{{ row.room_no }}</strong>
                <small>{{ $t(row.tower) }}</small>
              </div>
            </td>
            <td class="right money-cell">{{ $t(formatCurrencyFixed(row.rent)) }}</td>
            <td class="right money-cell">{{ $t(formatCurrencyFixed(row.received_amount)) }}</td>
            <td class="right money-cell" :class="{ danger: row.balance > 0 }">{{ $t(formatCurrencyFixed(row.balance)) }}</td>
            <td><span class="badge compact" :class="invoiceStatusTone(row.invoice_status)">{{ $t(row.invoice_status) }}</span></td>
            <td>{{ $t(row.invoice_no || '--') }}</td>
            <td>{{ $t(row.invoice_date || '--') }}</td>
            <td class="right money-cell">{{ $t(formatCurrencyFixed(row.invoice_amount)) }}</td>
            <td class="ledger-note-cell">{{ $t(row.invoice_notes || '--') }}</td>
            <td>
              <button v-if="isAdmin" class="btn btn-compact" @click="emit('edit', row)">{{ $t("编辑") }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="rows.length" class="table-footer ledger-table-footer">
      <div class="page-size-control">
        <span class="helper-text">{{ $t("每页显示") }}</span>
        <div class="page-size-switch" data-testid="invoice-ledger-page-size">
          <button
            v-for="option in pageSizeOptions"
            :key="option.value"
            type="button"
            :class="{ active: pager.pageSize === option.value }"
            @click="changePageSize(option.value)"
          >
            {{ $t(option.label) }}
          </button>
        </div>
      </div>
      <span class="helper-text">{{ $t("共 ") }}{{ $t(rows.length) }}{{ $t(" 条 · ") }}{{ $t(showingText) }}</span>
      <div class="spacer" />
      <button v-if="hasPagination" class="btn" :disabled="pager.page <= 1" @click="pager.page -= 1">{{ $t("上一页") }}</button>
      <span v-if="hasPagination">{{ $t(currentPageLabel) }}</span>
      <button
        v-if="hasPagination"
        class="btn"
        :disabled="pager.page >= totalPages"
        @click="pager.page += 1"
      >{{ $t(" 下一页 ") }}</button>
    </div>
    <div v-else class="compact-empty slim">{{ $t("当前筛选下暂无发票台账记录") }}</div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import { formatCurrencyFixed } from '../lib/format'
import { invoiceStatusTone } from '../lib/receivableStatusTone'
import {
  clampPager,
  listTotalPages,
  pageLabel,
  pageRows,
  showingText as buildShowingText
} from '../lib/table'

const props = defineProps({
  ledger: { type: Object, default: null },
  rows: { type: Array, required: true },
  filters: { type: Object, required: true },
  pager: { type: Object, required: true },
  pageSizeOptions: { type: Array, required: true },
  isAdmin: { type: Boolean, default: false }
})

const emit = defineEmits(['refresh', 'export', 'edit'])

const totalPages = computed(() => listTotalPages(props.rows.length, props.pager.pageSize))
const hasPagination = computed(() => props.pager.pageSize !== 0 && totalPages.value > 1)
const paginatedRows = computed(() => pageRows(props.rows, props.pager))
const currentPageLabel = computed(() => pageLabel(props.pager, totalPages.value))
const showingText = computed(() => buildShowingText(props.rows.length, props.pager))

watch(
  () => props.rows.length,
  () => {
    clampPager(props.pager, totalPages.value)
  }
)

function changePageSize(size) {
  props.pager.pageSize = size
  props.pager.page = 1
}
</script>
