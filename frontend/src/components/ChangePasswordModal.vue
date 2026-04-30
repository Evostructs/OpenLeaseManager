<script setup>
import { reactive, ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'

const props = defineProps({
  open: Boolean,
  busy: Boolean
})

const emit = defineEmits(['close', 'submit'])

const form = reactive({
  old_password: '',
  new_password: '',
  confirm_password: ''
})
const error = ref('')

watch(
  () => props.open,
  (open) => {
    if (!open) return
    form.old_password = ''
    form.new_password = ''
    form.confirm_password = ''
    error.value = ''
  }
)

function submit() {
  error.value = ''
  if (!form.old_password || !form.new_password || !form.confirm_password) {
    error.value = '请完整填写原密码和新密码'
    return
  }
  if (form.new_password !== form.confirm_password) {
    error.value = '两次输入的新密码不一致'
    return
  }
  emit('submit', {
    old_password: form.old_password,
    new_password: form.new_password
  })
}
</script>

<template>
  <BaseModal :open="open" title="修改密码" size="small" @close="$emit('close')">
    <div class="detail-list">
      <label class="form-field">
        <span class="section-label">原密码</span>
        <input v-model="form.old_password" type="password" class="input" autocomplete="current-password" />
      </label>
      <label class="form-field">
        <span class="section-label">新密码</span>
        <input v-model="form.new_password" type="password" class="input" autocomplete="new-password" />
        <small class="helper-text">至少 6 位，且需同时包含字母和数字。</small>
      </label>
      <label class="form-field">
        <span class="section-label">确认新密码</span>
        <input v-model="form.confirm_password" type="password" class="input" autocomplete="new-password" />
      </label>
      <p v-if="error" class="form-error">{{ error }}</p>
    </div>

    <template #footer>
      <button class="btn" :disabled="busy" @click="$emit('close')">取消</button>
      <button class="btn btn-primary" :disabled="busy" @click="submit">
        {{ busy ? '提交中' : '确认修改' }}
      </button>
    </template>
  </BaseModal>
</template>
