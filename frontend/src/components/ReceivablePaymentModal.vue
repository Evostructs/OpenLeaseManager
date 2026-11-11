<script setup>
import BaseModal from './BaseModal.vue'
import DatePickerInput from './DatePickerInput.vue'
import { formatCurrencyFixed } from '../lib/format'

// 月度应收「登记租金收款与核销」弹窗。
// 表单状态由父视图持有（reactive 对象引用传入，v-model 直接修改），
// 本组件只负责展示与事件转发，保存/删除/开收据仍由父视图执行。
defineProps({
  saving: Boolean,
  uncertain: Boolean,
  error: { type: String, default: '' },
  open: {
    type: Boolean,
    default: false
  },
  form: {
    type: Object,
    required: true
  },
  isAdmin: {
    type: Boolean,
    default: false
  }
})
const emit = defineEmits(['close', 'save', 'delete-payment', 'open-document'])
</script>

<template>
  <BaseModal :open="open" title="登记租金收款与核销" @close="!saving && !uncertain && emit('close')">
    <p v-if="error" class="inline-warning" role="alert" data-testid="receivable-save-error">{{ error }}</p>
    <fieldset class="receivable-payment-fields" :disabled="saving || uncertain" :aria-busy="saving">
    <div class="form-grid single">
      <div class="form-field">
        <span class="section-label">合同</span>
        <input :value="`${form.contract_no} · ${form.tenant_name}`" class="input" readonly />
      </div>
      <div class="form-grid">
        <div class="form-field">
          <span class="section-label">本月应收租金</span>
          <input :value="formatCurrencyFixed(form.total)" class="input" readonly />
        </div>
        <div class="form-field">
          <span class="section-label">现金实收</span>
          <input :value="formatCurrencyFixed(form.received_amount)" class="input" readonly />
        </div>
        <div class="form-field">
          <span class="section-label">核销调整</span>
          <input :value="formatCurrencyFixed(form.adjustment_amount)" class="input" readonly />
        </div>
        <div class="form-field">
          <span class="section-label">本月未收</span>
          <input :value="formatCurrencyFixed(form.balance)" class="input" readonly />
        </div>
      </div>

      <div class="detail-list">
        <div class="section-label">已登记明细</div>
        <div v-if="form.payments.length" class="table-wrapper compact-table">
          <table class="data-table" style="min-width: 760px">
            <thead>
              <tr>
                <th>日期</th>
                <th>类型</th>
                <th class="right">金额</th>
                <th>方式</th>
                <th>凭证/流水</th>
                <th>备注</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="payment in form.payments" :key="payment.id || `${payment.source}-${payment.amount}`">
                <td>{{ payment.payment_date || '--' }}</td>
                <td>{{ payment.allocation_type }}</td>
                <td class="right">{{ formatCurrencyFixed(payment.amount) }}</td>
                <td>{{ payment.payment_method || '--' }}</td>
                <td>{{ payment.reference_no || '--' }}</td>
                <td>{{ payment.notes || '--' }}</td>
                <td>
                  <button v-if="isAdmin && payment.id" class="btn btn-compact" @click="emit('open-document', 'receipt', form, payment)">收据</button>
                  <button
                    v-if="isAdmin && payment.id"
                    class="btn btn-compact"
                    :data-testid="`receivable-payment-delete-${payment.id}`"
                    @click="emit('delete-payment', payment)"
                  >
                    删除
                  </button>
                  <span v-if="!payment.id" class="helper-text">汇总记录</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="compact-empty slim">尚未登记收款或核销明细</div>
      </div>

      <div class="section-label">新增本月核销明细</div>
      <div class="form-grid">
        <label class="form-field">
          <span class="section-label">类型</span>
          <select v-model="form.allocation_type" class="select">
            <option value="收款">收款</option>
            <option value="冲抵">冲抵</option>
            <option value="减免">减免</option>
            <option value="坏账">坏账</option>
          </select>
        </label>
        <label class="form-field">
          <span class="section-label">金额</span>
          <input v-model.number="form.payment_amount" type="number" min="0" class="input" />
        </label>
        <div class="form-field">
          <span class="section-label">发生日期</span>
          <DatePickerInput v-model="form.payment_date" />
        </div>
        <label class="form-field">
          <span class="section-label">方式</span>
          <select v-model="form.payment_method" class="select">
            <option value="转账">转账</option>
            <option value="现金">现金</option>
            <option value="支票">支票</option>
            <option value="其他">其他</option>
          </select>
        </label>
        <label class="form-field">
          <span class="section-label">凭证/流水</span>
          <input v-model="form.reference_no" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">明细备注</span>
          <input v-model="form.payment_notes" class="input" />
        </label>
      </div>

      <div class="section-label">发票与催收状态</div>
      <div class="form-grid">
        <label class="form-field">
          <span class="section-label">发票状态</span>
          <select v-model="form.invoice_status" class="select">
            <option value="未开票">未开票</option>
            <option value="待开票">待开票</option>
            <option value="已开票">已开票</option>
            <option value="无需开票">无需开票</option>
            <option value="红冲/作废">红冲/作废</option>
          </select>
        </label>
        <label class="form-field">
          <span class="section-label">发票号</span>
          <input v-model="form.invoice_no" class="input" />
        </label>
        <div class="form-field">
          <span class="section-label">开票日期</span>
          <DatePickerInput v-model="form.invoice_date" />
        </div>
        <label class="form-field">
          <span class="section-label">开票金额</span>
          <input v-model.number="form.invoice_amount" type="number" min="0" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">税率</span>
          <input v-model.number="form.invoice_tax_rate" type="number" min="0" step="0.01" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">发票备注</span>
          <input v-model="form.invoice_notes" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">催收状态</span>
          <select v-model="form.dunning_status" class="select">
            <option value="未催收">未催收</option>
            <option value="已提醒">已提醒</option>
            <option value="催收中">催收中</option>
            <option value="已升级">已升级</option>
            <option value="无需催收">无需催收</option>
          </select>
        </label>
        <label class="form-field">
          <span class="section-label">催收负责人</span>
          <input v-model="form.dunning_owner" class="input" />
        </label>
        <div class="form-field">
          <span class="section-label">最近催收日</span>
          <DatePickerInput v-model="form.last_dunning_date" />
        </div>
        <div class="form-field">
          <span class="section-label">下次跟进日</span>
          <DatePickerInput v-model="form.next_follow_up_date" />
        </div>
        <label class="form-field full">
          <span class="section-label">催收备注</span>
          <textarea v-model="form.dunning_notes" class="textarea compact-textarea" />
        </label>
        <label class="form-field full">
          <span class="section-label">月度备注</span>
          <textarea v-model="form.notes" class="textarea compact-textarea" />
        </label>
      </div>
    </div>
    </fieldset>
    <template #footer>
      <button class="btn" :disabled="saving || uncertain" @click="emit('close')">取消</button>
      <button v-if="isAdmin" class="btn btn-primary" :disabled="saving" data-testid="receivable-save" @click="emit('save')">{{ saving ? '正在确认…' : uncertain ? '核对并重试' : '保存' }}</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.receivable-payment-fields { border: 0; margin: 0; padding: 0; min-width: 0; }
</style>
