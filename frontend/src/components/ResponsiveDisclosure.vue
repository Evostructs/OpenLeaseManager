<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
defineProps({ title: { type: String, required: true } })
const compact = ref(typeof window !== 'undefined' && window.matchMedia('(max-width: 640px)').matches)
const expanded = ref(false)
let media
const update = () => { compact.value = media.matches }
onMounted(() => { media = window.matchMedia('(max-width: 640px)'); update(); media.addEventListener('change', update) })
onBeforeUnmount(() => media?.removeEventListener('change', update))
function toggle(event) { if (compact.value) expanded.value = event.target.open }
</script>
<template>
  <details class="responsive-disclosure" :open="!compact || expanded" @toggle="toggle">
    <summary>{{ title }}</summary>
    <slot />
  </details>
</template>
<style scoped>
.responsive-disclosure { min-width: 0; }
.responsive-disclosure > summary { display: none; }
@media (max-width: 640px) {
  .responsive-disclosure { border-bottom: 1px solid var(--border); }
  .responsive-disclosure > summary { display: list-item; min-height: 44px; padding: 12px; cursor: pointer; font-weight: 600; }
  .responsive-disclosure > summary:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
}
</style>
