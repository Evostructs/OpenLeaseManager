<script setup>
import AnimatedNumber from './AnimatedNumber.vue'

defineProps({
  label: {
    type: String,
    required: true
  },
  value: {
    type: [String, Number],
    required: true
  },
  helper: {
    type: String,
    default: ''
  },
  tone: {
    type: String,
    default: ''
  }
})
</script>

<template>
  <div class="metric-tile" :class="tone">
    <span>{{ label }}</span>
    <AnimatedNumber :value="value" />
    <small v-if="helper">{{ helper }}</small>
    <slot></slot>
  </div>
</template>
