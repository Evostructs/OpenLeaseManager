<script setup>
import { computed } from 'vue'
import { statusTone } from '../lib/format'

const props = defineProps({
  status: {
    type: String,
    default: ''
  }
})

const tone = computed(() => statusTone(props.status))
</script>

<template>
  <span class="badge" :class="tone">{{ status || '未标注' }}</span>
</template>
