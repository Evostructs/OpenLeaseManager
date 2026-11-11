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
  <BaseModal :open="open" :title="$t(&quot;登记租金收款与核销&quot;)" @close="!saving && !uncertain && emit('close')">
    <p v-if="error" class="inline-warning" role="alert" data-testid="receivable-save-error">{{ $t(error) }}</p>
    <fieldset class="receivable-payment-fields" :disabled="saving || uncertain" :aria-busy="saving">
    <div class="form-grid single">
      <div class="form-field">
        <span class="section-label">{{ $t("合同") }}</span>
        <input :value="`${form.contract_no} · ${form.tenant_name}`" class="input" readonly />
      </div>
      <div class="form-grid">
        <div class="form-field">
          <span class="section-label">{{ $t("本月应收租金") }}</span>
          <input :value="formatCurrencyFixed(form.total)" class="input" readonly />
        </div>
        <div class="form-field">
          <span class="section-label">{{ $t("现金实收") }}</span>
          <input :value="formatCurrencyFixed(form.received_amount)" class="input" readonly />
        </div>
        <div class="form-field">
          <span class="section-label">{{ $t("核销调整") }}</span>
          <input :value="formatCurrencyFixed(form.adjustment_amount)" class="input" readonly />
        </div>
        <div class="form-field">
          <span class="section-label">{{ $t("本月未收") }}</span>
          <input :value="formatCurrencyFixed(form.balance)" class="input" readonly />
        </div>
      </div>

      <div class="detail-list">
        <div class="section-label">{{ $t("已登记明细") }}</div>
        <div v-if="form.payments.length" class="table-wrapper compact-table">
          <table class="data-table" style="min-width: 760px">
            <thead>
              <tr>
                <th>{{ $t("日期") }}</th>
                <th>{{ $t("类型") }}</th>
                <th class="right">{{ $t("金额") }}</th>
                <th>{{ $t("方式") }}</th>
                <th>{{ $t("凭证/流水") }}</th>
                <th>{{ $t("备注") }}</th>
                <th>{{ $t("操作") }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="payment in form.payments" :key="payment.id || `${payment.source}-${payment.amount}`">
                <td>{{ $t(payment.payment_date || '--') }}</td>
                <td>{{ $t(payment.allocation_type) }}</td>
                <td class="right">{{ $t(formatCurrencyFixed(payment.amount)) }}</td>
                <td>{{ $t(payment.payment_method || '--') }}</td>
                <td>{{ $t(payment.reference_no || '--') }}</td>
                <td>{{ payment.notes || '--' }}</td>
                <td>
                  <button v-if="isAdmin && payment.id" class="btn btn-compact" @click="emit('open-document', 'receipt', form, payment)">{{ $t("收据") }}</button>
                  <button
                    v-if="isAdmin && payment.id"
                    class="btn btn-compact"
                    :data-testid="`receivable-payment-delete-${payment.id}`"
                    @click="emit('delete-payment', payment)"
                  >{{ $t(" 删除 ") }}</button>
                  <span v-if="!payment.id" class="helper-text">{{ $t("汇总记录") }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="compact-empty slim">{{ $t("尚未登记收款或核销明细") }}</div>
      </div>

      <div class="section-label">{{ $t("新增本月核销明细") }}</div>
      <div class="form-grid">
        <label class="form-field">
          <span class="section-label">{{ $t("类型") }}</span>
          <select v-model="form.allocation_type" class="select">
            <option value="收款">{{ $t("收款") }}</option>
            <option value="冲抵">{{ $t("冲抵") }}</option>
            <option value="减免">{{ $t("减免") }}</option>
            <option value="坏账">{{ $t("坏账") }}</option>
          </select>
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("金额") }}</span>
          <input v-model.number="form.payment_amount" type="number" min="0" class="input" />
        </label>
        <div class="form-field">
          <span class="section-label">{{ $t("发生日期") }}</span>
          <DatePickerInput v-model="form.payment_date" />
        </div>
        <label class="form-field">
          <span class="section-label">{{ $t("方式") }}</span>
          <select v-model="form.payment_method" class="select">
            <option value="转账">{{ $t("转账") }}</option>
            <option value="现金">{{ $t("现金") }}</option>
            <option value="支票">{{ $t("支票") }}</option>
            <option value="其他">{{ $t("其他") }}</option>
          </select>
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("凭证/流水") }}</span>
          <input v-model="form.reference_no" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("明细备注") }}</span>
          <input v-model="form.payment_notes" class="input" />
        </label>
      </div>

      <div class="section-label">{{ $t("发票与催收状态") }}</div>
      <div class="form-grid">
        <label class="form-field">
          <span class="section-label">{{ $t("发票状态") }}</span>
          <select v-model="form.invoice_status" class="select">
            <option value="未开票">{{ $t("未开票") }}</option>
            <option value="待开票">{{ $t("待开票") }}</option>
            <option value="已开票">{{ $t("已开票") }}</option>
            <option value="无需开票">{{ $t("无需开票") }}</option>
            <option value="红冲/作废">{{ $t("红冲/作废") }}</option>
          </select>
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("发票号") }}</span>
          <input v-model="form.invoice_no" class="input" />
        </label>
        <div class="form-field">
          <span class="section-label">{{ $t("开票日期") }}</span>
          <DatePickerInput v-model="form.invoice_date" />
        </div>
        <label class="form-field">
          <span class="section-label">{{ $t("开票金额") }}</span>
          <input v-model.number="form.invoice_amount" type="number" min="0" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("税率") }}</span>
          <input v-model.number="form.invoice_tax_rate" type="number" min="0" step="0.01" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("发票备注") }}</span>
          <input v-model="form.invoice_notes" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("催收状态") }}</span>
          <select v-model="form.dunning_status" class="select">
            <option value="未催收">{{ $t("未催收") }}</option>
            <option value="已提醒">{{ $t("已提醒") }}</option>
            <option value="催收中">{{ $t("催收中") }}</option>
            <option value="已升级">{{ $t("已升级") }}</option>
            <option value="无需催收">{{ $t("无需催收") }}</option>
          </select>
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("催收负责人") }}</span>
          <input v-model="form.dunning_owner" class="input" />
        </label>
        <div class="form-field">
          <span class="section-label">{{ $t("最近催收日") }}</span>
          <DatePickerInput v-model="form.last_dunning_date" />
        </div>
        <div class="form-field">
          <span class="section-label">{{ $t("下次跟进日") }}</span>
          <DatePickerInput v-model="form.next_follow_up_date" />
        </div>
        <label class="form-field full">
          <span class="section-label">{{ $t("催收备注") }}</span>
          <textarea v-model="form.dunning_notes" class="textarea compact-textarea" />
        </label>
        <label class="form-field full">
          <span class="section-label">{{ $t("月度备注") }}</span>
          <textarea v-model="form.notes" class="textarea compact-textarea" />
        </label>
      </div>
    </div>
    </fieldset>
    <template #footer>
      <button class="btn" :disabled="saving || uncertain" @click="emit('close')">{{ $t("取消") }}</button>
      <button v-if="isAdmin" class="btn btn-primary" :disabled="saving" data-testid="receivable-save" @click="emit('save')">{{ $t(saving ? '正在确认…' : uncertain ? '核对并重试' : '保存') }}</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.receivable-payment-fields { border: 0; margin: 0; padding: 0; min-width: 0; }
</style>
