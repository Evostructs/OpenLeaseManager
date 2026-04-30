<script setup>
import { reactive, watch } from 'vue'
import BaseModal from './BaseModal.vue'

const blank = () => ({
  id: null,
  contract_no: '',
  tower: '',
  room_no: '',
  tenant_name: '',
  area: 0,
  unit_price: 0,
  lease_type: '新租',
  free_period: 0,
  monthly_rent: 0,
  broker_fee: 0,
  start_date: '',
  end_date: '',
  disposal: '',
  contact_person: '',
  contact_phone: '',
  contact_email: '',
  deposit: 0,
  deposit_status: '',
  property_fee_rate: 0,
  prev_contract_no: '',
  notes: '',
  last_updated: ''
})

const props = defineProps({
  open: Boolean,
  contract: {
    type: Object,
    default: null
  },
  canEdit: Boolean
})

const emit = defineEmits(['close', 'save'])
const form = reactive(blank())

watch(
  () => props.contract,
  (value) => {
    Object.assign(form, blank(), value || {})
    form.last_updated = value?.updated_at || ''
  },
  { immediate: true }
)

function submit() {
  emit('save', { ...form })
}
</script>

<template>
  <BaseModal :open="open" :title="form.id ? '编辑合同' : '新建合同'" @close="emit('close')">
    <div class="form-grid">
      <label class="form-field">
        <span class="section-label">合同编号</span>
        <input v-model="form.contract_no" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">楼栋/区域</span>
        <input v-model="form.tower" class="input" placeholder="如 A 座、1 号楼、商业区" />
      </label>
      <label class="form-field">
        <span class="section-label">室号</span>
        <input v-model="form.room_no" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">客户名称</span>
        <input v-model="form.tenant_name" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">面积</span>
        <input v-model.number="form.area" type="number" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">租金单价</span>
        <input v-model.number="form.unit_price" type="number" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">租赁类型</span>
        <select v-model="form.lease_type" class="select">
          <option value="新租">新租</option>
          <option value="续租">续租</option>
          <option value="新签">新签</option>
        </select>
      </label>
      <label class="form-field">
        <span class="section-label">免租期（月）</span>
        <input v-model.number="form.free_period" type="number" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">月租金</span>
        <input v-model.number="form.monthly_rent" type="number" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">中介佣金</span>
        <input v-model.number="form.broker_fee" type="number" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">起租日</span>
        <input v-model="form.start_date" type="date" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">终止日</span>
        <input v-model="form.end_date" type="date" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">到期处置</span>
        <select v-model="form.disposal" class="select">
          <option value="">待处理</option>
          <option value="已续租">已续租</option>
          <option value="已退租">已退租</option>
          <option value="逾期占用">逾期占用</option>
        </select>
      </label>
      <label class="form-field">
        <span class="section-label">关联合同编号</span>
        <input v-model="form.prev_contract_no" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">联系人</span>
        <input v-model="form.contact_person" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">联系电话</span>
        <input v-model="form.contact_phone" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">邮箱</span>
        <input v-model="form.contact_email" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">押金</span>
        <input v-model.number="form.deposit" type="number" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">押金状态</span>
        <select v-model="form.deposit_status" class="select">
          <option value="">未标注</option>
          <option value="已收">已收</option>
          <option value="已退">已退</option>
          <option value="部分退还">部分退还</option>
          <option value="未收">未收</option>
          <option value="待收">待收</option>
        </select>
      </label>
      <label class="form-field">
        <span class="section-label">物业费单价</span>
        <input v-model.number="form.property_fee_rate" type="number" class="input" />
      </label>
      <label class="form-field full">
        <span class="section-label">备注</span>
        <textarea v-model="form.notes" class="textarea" />
      </label>
    </div>

    <template #footer>
      <button class="btn" @click="emit('close')">取消</button>
      <button class="btn btn-primary" :disabled="!canEdit" @click="submit">保存</button>
    </template>
  </BaseModal>
</template>
