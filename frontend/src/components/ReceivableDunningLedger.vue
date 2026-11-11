<template>
  <div>
    <div class="workbench-header">
      <div>
        <h4>催收台账</h4>
      </div>
      <button v-if="rows.length" class="btn" @click="emit('export')">导出催收台账</button>
    </div>
    <div class="toolbar tool-toolbar compact-ledger-toolbar">
      <select v-model="filters.dunning_status" class="select">
        <option value="">全部催收状态</option>
        <option value="未催收">未催收</option>
        <option value="已提醒">已提醒</option>
        <option value="催收中">催收中</option>
        <option value="已升级">已升级</option>
        <option value="无需催收">无需催收</option>
      </select>
      <input v-model="filters.dunning_owner" class="input ledger-owner-input" placeholder="负责人" @keyup.enter="emit('refresh')" />
      <select v-model="filters.overdue_only" class="select">
        <option value="">全部本月逾期状态</option>
        <option value="true">仅本月逾期</option>
        <option value="false">本月未逾期</option>
      </select>
      <label class="inline-checkbox">
        <input v-model="filters.focus" type="checkbox" />
        只看待跟进
      </label>
      <button class="btn btn-primary" @click="emit('refresh')">刷新催收台账</button>
    </div>
    <div v-if="ledger" class="tool-summary-grid compact-stats ledger-summary-grid">
      <div class="tool-summary-card">
        <span>催收记录</span>
        <strong>{{ ledger.count || 0 }}</strong>
      </div>
      <div class="tool-summary-card">
        <span>本月未收</span>
        <strong>{{ formatCurrencyFixed(ledger.total_balance) }}</strong>
      </div>
      <div class="tool-summary-card">
        <span>本月逾期</span>
        <strong>{{ formatCurrencyFixed(ledger.total_overdue_balance) }}</strong>
      </div>
      <div class="tool-summary-card">
        <span>跟进临近</span>
        <strong>{{ ledger.follow_up_due_count || 0 }}</strong>
      </div>
    </div>
    <div v-if="rows.length" class="table-wrapper compact-table">
      <table class="data-table ledger-table dunning-ledger-table">
        <thead>
          <tr>
            <th>合同 / 客户</th>
            <th>房源</th>
            <th class="right">应收</th>
            <th class="right">已收</th>
            <th class="right">本月未收</th>
            <th class="right">本月逾期</th>
            <th>本月逾期天数</th>
            <th>催收状态</th>
            <th>负责人</th>
            <th>跟进</th>
            <th>备注</th>
            <th>操作</th>
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
                <small>{{ row.tower }}</small>
              </div>
            </td>
            <td class="right money-cell">{{ formatCurrencyFixed(row.rent) }}</td>
            <td class="right money-cell">{{ formatCurrencyFixed(row.received_amount) }}</td>
            <td class="right money-cell" :class="{ danger: row.balance > 0 }">{{ formatCurrencyFixed(row.balance) }}</td>
            <td class="right money-cell" :class="{ danger: row.overdue_balance > 0 }">{{ formatCurrencyFixed(row.overdue_balance) }}</td>
            <td><span class="aging-text" :class="{ danger: row.aging_days }">{{ row.aging_days ? `${row.aging_days} 天` : '--' }}</span></td>
            <td><span class="badge compact" :class="dunningStatusTone(row.dunning_status)">{{ row.dunning_status }}</span></td>
            <td>{{ row.dunning_owner || '--' }}</td>
            <td>
              <div class="cell-stack compact">
                <span>最近 {{ row.last_dunning_date || '--' }}</span>
                <small>下次 {{ row.next_follow_up_date || '--' }}</small>
              </div>
            </td>
            <td class="ledger-note-cell">{{ row.dunning_notes || '--' }}</td>
            <td class="receivable-action-cell">
              <button v-if="isAdmin" class="btn btn-compact" @click="emit('edit', row)">编辑</button>
              <button v-if="isAdmin" class="btn btn-compact" @click="emit('open-document', row)">催收</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="rows.length" class="table-footer ledger-table-footer">
      <div class="page-size-control">
        <span class="helper-text">每页显示</span>
        <div class="page-size-switch" data-testid="dunning-ledger-page-size">
          <button
            v-for="option in pageSizeOptions"
            :key="option.value"
            type="button"
            :class="{ active: pager.pageSize === option.value }"
            @click="changePageSize(option.value)"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
      <span class="helper-text">共 {{ rows.length }} 条 · {{ showingText }}</span>
      <div class="spacer" />
      <button v-if="hasPagination" class="btn" :disabled="pager.page <= 1" @click="pager.page -= 1">上一页</button>
      <span v-if="hasPagination">{{ currentPageLabel }}</span>
      <button
        v-if="hasPagination"
        class="btn"
        :disabled="pager.page >= totalPages"
        @click="pager.page += 1"
      >
        下一页
      </button>
    </div>
    <div v-else class="compact-empty slim">当前筛选下暂无催收台账记录</div>
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
