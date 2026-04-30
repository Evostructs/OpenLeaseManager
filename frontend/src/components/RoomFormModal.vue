<script setup>
import { reactive, watch } from 'vue'
import BaseModal from './BaseModal.vue'

const blank = () => ({
  tower: '',
  floor: '',
  room_no: '',
  room_type: '办公',
  area: 0,
  is_leasable: true,
  notes: ''
})

const props = defineProps({
  open: Boolean,
  room: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['close', 'save'])
const form = reactive(blank())

watch(
  () => props.room,
  (value) => Object.assign(form, blank(), value || {}),
  { immediate: true }
)
</script>

<template>
  <BaseModal :open="open" :title="room?.id ? '编辑房间' : '新增房间'" size="small" @close="emit('close')">
    <div class="form-grid">
      <label class="form-field">
        <span class="section-label">楼栋/区域</span>
        <input v-model="form.tower" class="input" placeholder="如 A 座、1 号楼、商业区" />
      </label>

      <label class="form-field">
        <span class="section-label">楼层</span>
        <input v-model="form.floor" class="input" />
      </label>

      <label class="form-field">
        <span class="section-label">室号</span>
        <input v-model="form.room_no" class="input" />
      </label>

      <label class="form-field">
        <span class="section-label">类型</span>
        <input v-model="form.room_type" class="input" placeholder="办公 / 商业 / 自定义业态" />
      </label>

      <label class="form-field">
        <span class="section-label">面积</span>
        <input v-model.number="form.area" type="number" class="input" />
      </label>

      <label class="form-field">
        <span class="section-label">可出租</span>
        <label class="checkbox-row">
          <input v-model="form.is_leasable" type="checkbox" />
          <span>纳入出租统计</span>
        </label>
      </label>

      <label class="form-field full">
        <span class="section-label">备注</span>
        <textarea v-model="form.notes" class="textarea" />
      </label>
    </div>

    <template #footer>
      <button class="btn" @click="emit('close')">取消</button>
      <button class="btn btn-primary" @click="emit('save', { ...form })">保存</button>
    </template>
  </BaseModal>
</template>
