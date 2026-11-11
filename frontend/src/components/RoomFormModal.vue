<script setup>
import { computed, reactive, watch } from 'vue'
import { profileSpaceOptions } from '../lib/roomsDomain'
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
  },
  operationsProfile: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits(['close', 'save'])
const form = reactive(blank())
const configuredSpaceOptions = computed(() => profileSpaceOptions(props.operationsProfile))
const roomSpaceOptions = computed(() => {
  const options = [...configuredSpaceOptions.value]
  const current = String(form.tower || '').trim()
  if (current && !options.some((item) => item.value === current)) {
    options.push({ code: current, value: current, label: current, kind: '' })
  }
  return options
})
const defaultSpaceValue = computed(() => (
  configuredSpaceOptions.value.length === 1 ? configuredSpaceOptions.value[0].value : ''
))

watch(
  () => [props.room, props.operationsProfile],
  ([value]) => {
    Object.assign(form, blank(), value || {})
    if (!form.tower) form.tower = defaultSpaceValue.value
  },
  { immediate: true }
)
</script>

<template>
  <BaseModal :open="open" :title="room?.id ? '编辑房间' : '新增房间'" size="small" @close="emit('close')">
    <div class="form-grid">
      <label class="form-field">
        <span class="section-label">空间分区</span>
        <select v-model="form.tower" class="select">
          <option v-for="option in roomSpaceOptions" :key="option.code || option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
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
        <select v-model="form.room_type" class="select">
          <option value="办公">办公</option>
          <option value="商业">商业</option>
          <option value="餐饮">餐饮</option>
          <option value="健身">健身</option>
        </select>
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
