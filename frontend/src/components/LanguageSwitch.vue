<script setup>
import { locale, setLocale } from '../i18n/index.js'
</script>

<template>
  <button
    type="button"
    class="btn language-switch"
    data-testid="language-switch"
    :aria-label="locale === 'en' ? '切换到中文' : 'Switch to English'"
    :title="locale === 'en' ? '切换到中文' : 'Switch to English'"
    @click="setLocale(locale === 'en' ? 'zh-CN' : 'en')"
  >
    <span aria-hidden="true">◎</span>
    <span :lang="locale === 'en' ? 'zh-CN' : 'en'">{{ locale === 'en' ? '中文' : 'English' }}</span>
  </button>
</template>

<style scoped>
.language-switch { display: inline-flex; align-items: center; gap: 6px; flex: 0 0 auto; white-space: nowrap; }
</style>
