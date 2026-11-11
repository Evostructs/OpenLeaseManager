<script setup>
defineProps({
  rows: {
    type: Number,
    default: 6
  },
  columns: {
    type: Number,
    default: 5
  }
})
</script>

<template>
  <div class="table-skeleton" :style="{ '--skeleton-columns': columns }" data-testid="table-skeleton" aria-hidden="true">
    <div class="table-skeleton-row table-skeleton-head">
      <span v-for="column in columns" :key="`head-${column}`" class="skeleton-line"></span>
    </div>
    <div v-for="row in rows" :key="`row-${row}`" class="table-skeleton-row">
      <span v-for="column in columns" :key="`cell-${row}-${column}`" class="skeleton-line"></span>
    </div>
  </div>
</template>
