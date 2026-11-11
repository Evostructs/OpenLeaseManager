<script setup>
import EChartPanel from './EChartPanel.vue'

defineProps({
  title: {
    type: String,
    required: true
  },
  subtitle: {
    type: String,
    default: ''
  },
  insight: {
    type: String,
    default: ''
  },
  option: {
    type: Object,
    default: () => ({})
  },
  height: {
    type: Number,
    default: 220
  },
  empty: {
    type: Boolean,
    default: false
  },
  emptyText: {
    type: String,
    default: '暂无数据'
  },
  tone: {
    type: String,
    default: 'info'
  }
})
</script>

<template>
  <article class="chart-card" :class="`tone-${tone}`">
    <header class="chart-card-header">
      <div class="chart-card-copy">
        <slot name="label">
          <span class="section-label">重点观察</span>
        </slot>
        <h3>{{ title }}</h3>
        <p v-if="subtitle">{{ subtitle }}</p>
      </div>
      <div v-if="$slots.action" class="chart-card-actions">
        <slot name="action" />
      </div>
    </header>

    <EChartPanel
      :option="option"
      :height="height"
      :empty="empty"
      :empty-text="emptyText"
    />

    <p v-if="insight" class="chart-card-insight">{{ insight }}</p>
    <slot />
  </article>
</template>
