<template>
  <div>
    <div class="workbench-header">
      <div>
        <h4>{{ $t("催收台账") }}</h4>
      </div>
      <button v-if="rows.length" class="btn" @click="emit('export')">{{ $t("导出催收台账") }}</button>
    </div>
    <div class="toolbar tool-toolbar compact-ledger-toolbar">
      <select v-model="filters.dunning_status" class="select">
        <option value="">{{ $t("全部催收状态") }}</option>
        <option value="未催收">{{ $t("未催收") }}</option>
        <option value="已提醒">{{ $t("已提醒") }}</option>
        <option value="催收中">{{ $t("催收中") }}</option>
        <option value="已升级">{{ $t("已升级") }}</option>
        <option value="无需催收">{{ $t("无需催收") }}</option>
      </select>
      <input v-model="filters.dunning_owner" class="input ledger-owner-input" :placeholder="$t(&quot;负责人&quot;)" @keyup.enter="emit('refresh')" />
      <select v-model="filters.overdue_only" class="select">
        <option value="">{{ $t("全部本月逾期状态") }}</option>
        <option value="true">{{ $t("仅本月逾期") }}</option>
        <option value="false">{{ $t("本月未逾期") }}</option>
      </select>
      <label class="inline-checkbox">
        <input v-model="filters.focus" type="checkbox" />{{ $t(" 只看待跟进 ") }}</label>
      <button class="btn btn-primary" @click="emit('refresh')">{{ $t("刷新催收台账") }}</button>
    </div>
    <div v-if="ledger" class="tool-summary-grid compact-stats ledger-summary-grid">
      <div class="tool-summary-card">
        <span>{{ $t("催收记录") }}</span>
        <strong>{{ $t(ledger.count || 0) }}</strong>
      </div>
      <div class="tool-summary-card">
        <span>{{ $t("本月未收") }}</span>
        <strong>{{ $t(formatCurrencyFixed(ledger.total_balance)) }}</strong>
      </div>
      <div class="tool-summary-card">
        <span>{{ $t("本月逾期") }}</span>
        <strong>{{ $t(formatCurrencyFixed(ledger.total_overdue_balance)) }}</strong>
      </div>
      <div class="tool-summary-card">
        <span>{{ $t("跟进临近") }}</span>
        <strong>{{ $t(ledger.follow_up_due_count || 0) }}</strong>
      </div>
    </div>
    <div v-if="rows.length" class="table-wrapper compact-table">
      <table class="data-table ledger-table dunning-ledger-table">
        <thead>
          <tr>
            <th>{{ $t("合同 / 客户") }}</th>
            <th>{{ $t("房源") }}</th>
            <th class="right">{{ $t("应收") }}</th>
            <th class="right">{{ $t("已收") }}</th>
            <th class="right">{{ $t("本月未收") }}</th>
            <th class="right">{{ $t("本月逾期") }}</th>
            <th>{{ $t("本月逾期天数") }}</th>
            <th>{{ $t("催收状态") }}</th>
            <th>{{ $t("负责人") }}</th>
            <th>{{ $t("跟进") }}</th>
            <th>{{ $t("备注") }}</th>
            <th>{{ $t("操作") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in paginatedRows" :key="`dunning-${row.contract_id}`">
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
            <td class="right money-cell" :class="{ danger: row.overdue_balance > 0 }">{{ $t(formatCurrencyFixed(row.overdue_balance)) }}</td>
            <td><span class="aging-text" :class="{ danger: row.aging_days }">{{ $t(row.aging_days ? `${row.aging_days} 天` : '--') }}</span></td>
            <td><span class="badge compact" :class="dunningStatusTone(row.dunning_status)">{{ $t(row.dunning_status) }}</span></td>
            <td>{{ $t(row.dunning_owner || '--') }}</td>
            <td>
              <div class="cell-stack compact">
                <span>{{ $t("最近 ") }}{{ $t(row.last_dunning_date || '--') }}</span>
                <small>{{ $t("下次 ") }}{{ $t(row.next_follow_up_date || '--') }}</small>
              </div>
            </td>
            <td class="ledger-note-cell">{{ $t(row.dunning_notes || '--') }}</td>
            <td class="receivable-action-cell">
              <button v-if="isAdmin" class="btn btn-compact" @click="emit('edit', row)">{{ $t("编辑") }}</button>
              <button v-if="isAdmin" class="btn btn-compact" @click="emit('open-document', row)">{{ $t("催收") }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="rows.length" class="table-footer ledger-table-footer">
      <div class="page-size-control">
        <span class="helper-text">{{ $t("每页显示") }}</span>
        <div class="page-size-switch" data-testid="dunning-ledger-page-size">
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
    <div v-else class="compact-empty slim">{{ $t("当前筛选下暂无催收台账记录") }}</div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import { formatCurrencyFixed } from '../lib/format'
import { dunningStatusTone } from '../lib/receivableStatusTone'
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

const emit = defineEmits(['refresh', 'export', 'edit', 'open-document'])

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
