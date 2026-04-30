<script setup>
import { appConfig } from '../config'

defineProps({
  open: Boolean,
  currentTab: {
    type: String,
    required: true
  },
  items: {
    type: Array,
    required: true
  }
})

defineEmits(['select'])
</script>

<template>
  <aside class="sidebar" :class="{ open }">
    <div class="sidebar-brand">
      <div class="sidebar-brand-logo">
        <span class="sidebar-brand-mark">{{ appConfig.mark }}</span>
      </div>
      <div class="sidebar-brand-copy">
        <h1>{{ appConfig.title }}</h1>
        <p>{{ appConfig.subtitle }}</p>
      </div>
    </div>

    <nav class="nav-list">
      <button
        v-for="item in items"
        :key="item.key"
        class="nav-button"
        :class="{ active: currentTab === item.key }"
        :data-testid="`nav-${item.key}`"
        @click="$emit('select', item.key)"
      >
        <span class="nav-button-label">{{ item.label }}</span>
      </button>
    </nav>
  </aside>
</template>
