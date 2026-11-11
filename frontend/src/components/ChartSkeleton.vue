<script setup>
defineProps({
  height: {
    type: Number,
    default: 230
  }
})
</script>

<template>
  <div class="chart-skeleton" :style="{ minHeight: `${height}px` }" data-testid="chart-skeleton" aria-hidden="true">
    <span class="skeleton-line skeleton-line-short"></span>
    <div class="chart-skeleton-bars">
      <i v-for="index in 5" :key="index" :style="{ height: `${34 + index * 8}%` }"></i>
    </div>
  </div>
</template>
