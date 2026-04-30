<script setup>
import { reactive, ref } from 'vue'
import { appConfig } from '../config'

defineProps({
  needInit: Boolean,
  error: String,
  busy: Boolean
})

const emit = defineEmits(['login', 'register'])
const form = reactive({
  username: '',
  password: ''
})
const showPassword = ref(false)
const showForgotPassword = ref(false)
</script>

<template>
  <main class="login-shell">
    <section class="card login-card">
      <div class="login-hero">
        <span class="login-brand-mark">{{ appConfig.mark }}</span>
        <div>
          <h2 class="card-title login-brand-title">{{ appConfig.title }}</h2>
        </div>
      </div>

      <div class="form-grid">
        <label class="form-field full">
          <span class="section-label">用户名</span>
          <input v-model="form.username" class="input" data-testid="login-username" />
        </label>
        <label class="form-field full">
          <span class="section-label">密码</span>
          <span class="password-field">
            <input
              v-model="form.password"
              :type="showPassword ? 'text' : 'password'"
              class="input"
              data-testid="login-password"
            />
            <button
              type="button"
              class="password-toggle"
              :aria-label="showPassword ? '隐藏密码' : '显示密码'"
              @click="showPassword = !showPassword"
            >
              {{ showPassword ? '隐藏' : '显示' }}
            </button>
          </span>
        </label>
      </div>

      <p v-if="error" style="margin: 16px 0 0; color: var(--error-text)">{{ error }}</p>

      <div class="login-actions">
        <div class="btn-row">
          <button v-if="needInit" class="btn btn-primary" data-testid="register-submit" :disabled="busy" @click="emit('register', { ...form })">初始化管理员</button>
          <button v-else class="btn btn-primary" data-testid="login-submit" :disabled="busy" @click="emit('login', { ...form })">登录</button>
        </div>
        <button
          v-if="!needInit"
          type="button"
          class="forgot-password-link"
          @click="showForgotPassword = !showForgotPassword"
        >
          忘记密码？
        </button>
      </div>

      <p v-if="showForgotPassword && !needInit" class="forgot-password-note">
        请联系管理员重置密码。管理员可在「工具中心 - 用户管理」里为账号重置密码。
      </p>
    </section>
  </main>
</template>
