<script setup>
import ChartSkeleton from './ChartSkeleton.vue'
import MetricSkeleton from './MetricSkeleton.vue'
import TableSkeleton from './TableSkeleton.vue'

defineProps({
  variant: {
    type: String,
    default: 'default'
  }
})
</script>

<template>
  <section class="content page-skeleton" :class="`page-skeleton-${variant}`" data-testid="page-skeleton" aria-hidden="true">
    <article class="card page-skeleton-card">
      <div class="page-skeleton-heading">
        <span class="skeleton-line skeleton-line-label"></span>
        <strong class="skeleton-line skeleton-line-title"></strong>
      </div>
      <MetricSkeleton :count="4" />
      <div class="page-skeleton-grid">
        <ChartSkeleton />
        <ChartSkeleton />
      </div>
      <TableSkeleton :rows="6" :columns="5" />
    </article>
  </section>
</template>
