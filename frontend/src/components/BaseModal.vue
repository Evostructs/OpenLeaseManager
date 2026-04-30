<script setup>
defineProps({
  open: Boolean,
  title: {
    type: String,
    default: ''
  },
  size: {
    type: String,
    default: ''
  }
})

defineEmits(['close'])
</script>

<template>
  <div v-if="open" class="modal-backdrop" @click.self="$emit('close')">
    <section class="modal-panel" :class="{ small: size === 'small' }">
      <header class="modal-header">
        <div>
          <div class="section-label">弹窗</div>
          <h3 class="card-title">{{ title }}</h3>
        </div>
        <button class="icon-btn" @click="$emit('close')">×</button>
      </header>
      <div class="modal-body">
        <slot />
      </div>
      <footer v-if="$slots.footer" class="modal-footer">
        <slot name="footer" />
      </footer>
    </section>
  </div>
</template>
