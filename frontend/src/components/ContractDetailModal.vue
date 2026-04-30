<script setup>
import BaseModal from './BaseModal.vue'
import StatusBadge from './StatusBadge.vue'
import { formatAuditAction, formatAuditOperator, formatCurrency, formatDate } from '../lib/format'

defineProps({
  open: Boolean,
  contract: {
    type: Object,
    default: null
  },
  logs: {
    type: Array,
    default: () => []
  },
  rentPlan: {
    type: Array,
    default: () => []
  },
  rentSummary: {
    type: Object,
    default: () => ({})
  },
  showLogs: Boolean
})

defineEmits(['close'])
</script>

<template>
  <BaseModal :open="open" title="合同详情" @close="$emit('close')">
    <div v-if="contract" class="split-grid">
      <section class="card" style="padding:0;border:none;box-shadow:none">
        <div class="detail-list">
          <div>
            <div class="section-label">合同编号</div>
            <h3 class="card-title" style="margin-top:6px">{{ contract.contract_no }}</h3>
          </div>
          <div class="stat-grid">
            <div>
              <div class="section-label">状态</div>
              <StatusBadge :status="contract.status" />
            </div>
            <div>
              <div class="section-label">客户</div>
              <div>{{ contract.tenant_name || '--' }}</div>
            </div>
            <div>
              <div class="section-label">起止日期</div>
              <div>{{ formatDate(contract.start_date) }} - {{ formatDate(contract.end_date) }}</div>
            </div>
            <div>
              <div class="section-label">月租金</div>
              <div>{{ formatCurrency(contract.monthly_rent) }}</div>
            </div>
            <div>
              <div class="section-label">净租金</div>
              <div>{{ formatCurrency(contract.net_rent) }}</div>
            </div>
            <div>
              <div class="section-label">物业费/月</div>
              <div>{{ formatCurrency(contract.monthly_property_fee) }}</div>
            </div>
          </div>
          <div>
            <div class="section-label">备注</div>
            <p>{{ contract.notes || '暂无备注' }}</p>
          </div>
        </div>
      </section>

      <section class="card" style="padding:0;border:none;box-shadow:none">
        <div class="section-label">租金计划</div>
        <p class="helper-text">总月数 {{ rentSummary.total_months || 0 }}，总租金 {{ formatCurrency(rentSummary.total_rent || 0) }}</p>
        <div class="table-wrapper" style="margin-top:16px">
          <table class="data-table" style="min-width:620px">
            <thead>
              <tr>
                <th>月份</th>
                <th>租金</th>
                <th>物业费</th>
                <th>合计</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in rentPlan" :key="item.month">
                <td>{{ item.month }}</td>
                <td>{{ formatCurrency(item.rent) }}</td>
                <td>{{ formatCurrency(item.property_fee) }}</td>
                <td>{{ formatCurrency(item.total) }}</td>
              </tr>
              <tr v-if="!rentPlan.length">
                <td colspan="4" class="list-empty">暂无租金计划</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <section v-if="showLogs" class="card" style="margin-top:16px">
      <div class="section-label">操作记录</div>
      <div class="table-wrapper" style="margin-top:16px">
        <table class="data-table" style="min-width:680px">
          <thead>
            <tr>
              <th>时间</th>
              <th>操作人</th>
              <th>动作</th>
              <th>内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in logs" :key="log.id">
              <td>{{ formatDate(log.timestamp) }}</td>
              <td>{{ log.operator_label || formatAuditOperator(log.operator) }}</td>
              <td>{{ log.action_label || formatAuditAction(log.action) }}</td>
              <td>{{ log.changes }}</td>
            </tr>
            <tr v-if="!logs.length">
              <td colspan="4" class="list-empty">暂无操作记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </BaseModal>
</template>
