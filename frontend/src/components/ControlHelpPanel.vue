<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { activateModalFocus } from '../lib/modalFocus.js'

const props = defineProps({
  open: Boolean,
  title: {
    type: String,
    default: ''
  },
  content: {
    type: Object,
    default: () => ({
      title: '',
      look: [],
      next: [],
      terms: []
    })
  }
})

const emit = defineEmits(['close'])
const panelRef = ref(null)
let focusSession = null

function close() {
  if (focusSession && !focusSession.isTop()) return
  emit('close')
}

watch(() => props.open, async open => {
  const opener = document.activeElement
  if (!open) { focusSession?.release(); focusSession = null; return }
  await nextTick()
  if (props.open && panelRef.value && !focusSession) focusSession = activateModalFocus(panelRef.value, 100, opener)
}, { immediate: true })
onBeforeUnmount(() => focusSession?.release())
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop control-help-backdrop" @click.self="close" @keydown.esc.stop.prevent="close">
      <section
        ref="panelRef"
        class="modal-panel small control-help-panel"
        data-testid="control-help-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="control-help-title"
        tabindex="-1"
      >
        <header class="modal-header">
          <div>
            <div class="section-label">帮助</div>
            <h3 id="control-help-title" class="card-title">页面提示</h3>
          </div>
          <button
            type="button"
            class="icon-btn"
            aria-label="关闭帮助"
            data-testid="control-help-close"
            @click="close"
          >
            ×
          </button>
        </header>

        <div class="modal-body control-help-body">
          <p class="control-help-current" data-testid="control-help-current-page">
            {{ content.title || title }}
          </p>

          <section class="control-help-section">
            <h4>本页看什么</h4>
            <ul>
              <li v-for="item in content.look" :key="item">{{ item }}</li>
            </ul>
          </section>

          <section class="control-help-section">
            <h4>下一步做什么</h4>
            <ul>
              <li v-for="item in content.next" :key="item">{{ item }}</li>
            </ul>
          </section>

          <section class="control-help-section">
            <h4>常见口径</h4>
            <ul>
              <li v-for="item in content.terms" :key="item">{{ item }}</li>
            </ul>
          </section>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.control-help-backdrop {
  z-index: 400;
  align-items: flex-start;
  justify-content: flex-end;
  padding: 76px 24px 24px;
}

.control-help-panel {
  width: min(440px, calc(100vw - 48px));
  max-height: calc(100vh - 100px);
  border-radius: 16px;
  outline: none;
}

.control-help-body {
  display: grid;
  gap: 18px;
  padding: 22px 24px 24px;
}

.control-help-current {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.74);
  color: var(--text-strong);
  font-size: 15px;
  font-weight: 680;
}

.control-help-section {
  display: grid;
  gap: 8px;
}

.control-help-section h4 {
  margin: 0;
  color: var(--text-strong);
  font-size: 14px;
  font-weight: 680;
}

.control-help-section ul {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.control-help-section li {
  position: relative;
  padding-left: 14px;
  color: var(--text);
  font-size: 14px;
  line-height: 1.55;
  word-break: break-word;
}

.control-help-section li::before {
  position: absolute;
  top: 0.68em;
  left: 0;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--primary);
  content: "";
}

/* 超宽屏 body zoom 下 viewport 单位不随缩放（见 base-widescreen.css），
   max-height 按 1/zoom 归一 */
@media (min-width: 2560px) {
  .control-help-panel {
    max-height: calc(80vh - 100px);
  }
}

@media (min-width: 3840px) {
  .control-help-panel {
    max-height: calc(66.667vh - 100px);
  }
}

@media (max-width: 640px) {
  .control-help-backdrop {
    justify-content: center;
    padding: 70px 12px 12px;
  }

  .control-help-panel {
    width: min(100%, calc(100vw - 24px));
    max-height: calc(100vh - 82px);
  }

  .control-help-body {
    padding: 18px;
  }
}
</style>
