<script setup>
defineProps({
  count: {
    type: Number,
    default: 4
  }
})
</script>

<template>
  <div class="metric-skeleton-grid" data-testid="metric-skeleton" aria-hidden="true">
    <div v-for="index in count" :key="index" class="metric-skeleton-item">
      <span class="skeleton-line skeleton-line-short"></span>
      <strong class="skeleton-line skeleton-line-value"></strong>
      <small class="skeleton-line skeleton-line-medium"></small>
    </div>
  </div>
</template>
