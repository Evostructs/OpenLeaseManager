<script setup>
import { ref } from 'vue'
import { api } from '../lib/api.js'
import { currencies } from '../i18n/currency.js'
import { areaUnit, bookCurrency, currencyUnit, fxDate, fxAvailable, applyDisplayUnits, locale } from '../i18n/index.js'

const open = ref(false)
const selectedCurrency = ref(currencyUnit.value)
const selectedArea = ref(areaUnit.value)
const busy = ref(false)
const error = ref('')
async function apply() {
  busy.value = true
  error.value = ''
  try {
    const payload = await api('/api/auth/status?view_currency=' + encodeURIComponent(selectedCurrency.value))
    applyDisplayUnits(selectedCurrency.value, selectedArea.value, payload.presentation)
  }
  catch (failure) { error.value = failure.message; busy.value = false }
}
</script>

<template>
  <div class="display-units">
    <button class="badge units-trigger" data-testid="display-units-toggle" @click="open = !open" :aria-expanded="open">
      {{ currencyUnit }} · {{ areaUnit === 'ft2' ? 'ft²' : 'm²' }}
    </button>
    <small v-if="fxDate" data-testid="exchange-rate-date" :title="$t('参考汇率') + ' · ECB via Frankfurter · ' + $t('只读折算')">{{ fxDate }}</small>
    <small v-else-if="!fxAvailable" role="status">{{ $t('汇率暂不可用，已显示记账币种。') }}</small>
    <div v-if="open" class="units-panel" data-testid="display-units-panel">
      <label class="form-field">
        <span>{{ $t('查看币种') }}</span>
        <select v-model="selectedCurrency" class="select" data-testid="view-currency-select">
          <option v-for="item in currencies" :key="item.code" :value="item.code">{{ item.code }} — {{ locale === 'en' ? item.en : item.zh }}</option>
        </select>
      </label>
      <label class="form-field">
        <span>{{ $t('面积单位') }}</span>
        <select v-model="selectedArea" class="select" data-testid="area-unit-select">
          <option value="m2">m²</option><option value="ft2">ft²</option>
        </select>
      </label>
      <small>{{ $t('记账币种') }}: {{ bookCurrency }}{{ $t('；原始账目不变。') }}</small>
      <p v-if="error" role="alert">{{ error }}</p>
      <button class="btn btn-primary" data-testid="display-units-apply" :disabled="busy" @click="apply">{{ $t(busy ? '保存中' : '应用并刷新') }}</button>
    </div>
  </div>
</template>

<style scoped>
.display-units { position: relative; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.units-trigger { cursor: pointer; font: inherit; font-size: 11px; }
.display-units small { font-size: 11px; color: var(--muted, #667085); }
.units-panel { position: absolute; top: 100%; left: 0; z-index: 80; display: grid; gap: 12px; min-width: 240px; padding: 16px; border: 1px solid #d6dce5; border-radius: 12px; background: white; box-shadow: 0 12px 32px #18253426; }
.units-panel p { margin: 0; color: #b42318; font-size: 12px; }
</style>
