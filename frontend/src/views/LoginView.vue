<script setup>
import LanguageSwitch from '../components/LanguageSwitch.vue'
import { reactive, ref, watch } from 'vue'

const props = defineProps({
  needInit: Boolean,
  error: String,
  busy: Boolean
})

const emit = defineEmits(['login', 'register'])
const form = reactive({
  username: '',
  password: '',
  display_name: ''
})
const confirmPassword = ref('')
const confirmationInput = ref(null)
const confirmationError = ref('')
watch(() => [form.password, confirmPassword.value, props.needInit], () => { confirmationError.value = '' })
const showPassword = ref(false)
const showForgotPassword = ref(false)

function submitAuth() {
  if (props.busy) return
  if (props.needInit && (!confirmPassword.value || form.password !== confirmPassword.value)) {
    confirmationError.value = confirmPassword.value ? '两次输入的密码不一致' : '请再次输入密码'
    confirmationInput.value?.focus()
    return
  }
  const payload = { ...form }
  emit(props.needInit ? 'register' : 'login', payload)
}
</script>

<template>
  <main class="login-shell">
    <section class="login-entry" data-testid="login-entry">
      <aside class="login-brand-panel" data-testid="login-brand-panel">
        <div class="login-hero">
          <img src="/lease-manager-mark.svg" :alt="$t(&quot;OpenLeaseOps 租赁管理系统&quot;)" />
          <div>
            <p class="login-eyebrow">{{ $t("多楼宇租赁运营平台") }}</p>
            <h1 class="login-brand-title">{{ $t("租赁管理系统") }}</h1>
          </div>
        </div>

        <div class="login-signal-band" data-testid="login-signal-band">
          <div class="login-signal" data-testid="login-signal-contracts">
            <span>{{ $t("合同台账") }}</span>
            <strong>{{ $t("租期与面积") }}</strong>
          </div>
          <div class="login-signal success" data-testid="login-signal-receivables">
            <span>{{ $t("月度应收") }}</span>
            <strong>{{ $t("回款与逾期") }}</strong>
          </div>
          <div class="login-signal warning" data-testid="login-signal-risk">
            <span>{{ $t("风险中心") }}</span>
            <strong>{{ $t("到期与资料") }}</strong>
          </div>
        </div>
      </aside>

      <section class="card login-card" :class="{ 'is-init': props.needInit }" data-testid="login-card">
        <div class="login-language"><LanguageSwitch /></div>
        <div class="login-panel-head">
          <div>
            <p class="login-panel-kicker">{{ $t(props.needInit ? '首次启用' : '欢迎使用') }}</p>
            <h2 class="card-title login-panel-title">{{ $t(props.needInit ? '创建管理员' : '账号登录') }}</h2>
          </div>
          <span class="login-mode-badge" :class="{ init: props.needInit }" data-testid="login-mode">
            {{ $t(props.needInit ? '初始化' : '登录') }}
          </span>
        </div>

        <form class="login-form" data-testid="login-form" @submit.prevent="submitAuth">
          <div class="form-grid">
            <label v-if="props.needInit" class="form-field full">
              <span class="section-label">{{ $t("显示名称") }}</span>
              <input v-model="form.display_name" class="input" autocomplete="name" maxlength="50" data-testid="login-display-name" />
            </label>
            <label class="form-field full">
              <span class="section-label">{{ $t("用户名") }}</span>
              <input
                v-model="form.username"
                class="input"
                autocomplete="username"
                :required="props.needInit"
                :minlength="props.needInit ? 3 : undefined"
                :maxlength="props.needInit ? 32 : undefined"
                :pattern="props.needInit ? '[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]' : undefined"
                data-testid="login-username"
              />
              <span v-if="props.needInit" class="form-field-hint">{{ $t("3—32 位小写字母、数字、点、下划线或短横线，首尾使用字母或数字") }}</span>
            </label>
            <label class="form-field full">
              <span class="section-label">{{ $t("密码") }}</span>
              <span class="password-field">
                <input
                  v-model="form.password"
                  :type="showPassword ? 'text' : 'password'"
                  class="input"
                  :autocomplete="props.needInit ? 'new-password' : 'current-password'"
                  required
                  data-testid="login-password"
                />
                <button
                  type="button"
                  class="password-toggle"
                  :aria-label="$t(showPassword ? '隐藏密码' : '显示密码')"
                  @click="showPassword = !showPassword"
                >
                  {{ $t(showPassword ? '隐藏' : '显示') }}
                </button>
              </span>
            </label>
            <label v-if="props.needInit" class="form-field full">
              <span class="section-label">{{ $t("确认密码") }}</span>
              <input ref="confirmationInput" v-model="confirmPassword" type="password" class="input"
                autocomplete="new-password" required data-testid="login-confirm-password"
                :aria-invalid="!!confirmationError" :aria-describedby="confirmationError ? 'password-confirmation-error' : undefined" />
              <span v-if="confirmationError" id="password-confirmation-error" class="form-error" role="alert">{{ $t(confirmationError) }}</span>
            </label>
          </div>

          <p v-if="props.error" class="login-error" data-testid="login-error">{{ $t(props.error) }}</p>

          <div class="login-actions">
            <button
              v-if="props.needInit"
              type="submit"
              class="btn btn-primary"
              data-testid="register-submit"
              :disabled="props.busy"
            >{{ $t(" 创建管理员 ") }}</button>
            <button
              v-else
              type="submit"
              class="btn btn-primary"
              data-testid="login-submit"
              :disabled="props.busy"
            >{{ $t(" 进入系统 ") }}</button>
            <button
              v-if="!props.needInit"
              type="button"
              class="forgot-password-link"
              @click="showForgotPassword = !showForgotPassword"
            >{{ $t(" 忘记密码？ ") }}</button>
          </div>
        </form>

        <p v-if="showForgotPassword && !props.needInit" class="forgot-password-note">{{ $t(" 联系管理员重置密码。 ") }}</p>
      </section>
    </section>
  </main>
</template>

<style scoped>
.login-language { display: flex; justify-content: flex-end; margin-bottom: 12px; }
</style>
