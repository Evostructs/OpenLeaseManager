<script setup>
import { currencyUnit, areaUnit, fxDate } from '../i18n/index.js'
import LanguageSwitch from './LanguageSwitch.vue'
import { computed, inject, provide, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { activateModalFocus } from '../lib/modalFocus.js'

const MODAL_SCROLL_LOCK_CLASS = 'modal-scroll-locked'
const MODAL_LOCK_STATE_KEY = '__leaseManagerModalLockState'

const props = defineProps({
  open: Boolean,
  title: {
    type: String,
    default: ''
  },
  size: {
    type: String,
    default: ''
  },
  layerIndex: {
    type: Number,
    default: 0
  }
})

const parentLayer = inject('lease-modal-layer', null)
const effectiveLayer = computed(() => Math.max(props.layerIndex, parentLayer ? parentLayer.value + 1 : 0))
provide('lease-modal-layer', effectiveLayer)

const emit = defineEmits(['close'])

let ownsBodyLock = false
let focusSession = null
let opener = null
const panelRef = ref(null)
const modalTitleId = `${useId()}-title`

function modalLockState() {
  if (typeof window === 'undefined') return null
  if (!window[MODAL_LOCK_STATE_KEY]) {
    window[MODAL_LOCK_STATE_KEY] = { count: 0, scrollX: 0, scrollY: 0, restoreToken: 0 }
  }
  return window[MODAL_LOCK_STATE_KEY]
}

function lockBodyScroll() {
  if (ownsBodyLock || typeof document === 'undefined') return
  const state = modalLockState()
  if (!state) return
  // 新弹窗一旦开始接管页面，上一弹窗尚未执行的恢复任务立即失效。
  state.restoreToken = Number(state.restoreToken || 0) + 1
  if (state.count === 0) {
    state.scrollX = window.scrollX || 0
    state.scrollY = window.scrollY || 0
  }
  state.count += 1
  document.documentElement.classList.add(MODAL_SCROLL_LOCK_CLASS)
  document.body.style.overflow = ''
  ownsBodyLock = true
}

function restoreBodyScroll(state) {
  if (typeof window === 'undefined' || !state) return
  const left = Number(state.scrollX || 0)
  const top = Number(state.scrollY || 0)
  const restoreToken = Number(state.restoreToken || 0) + 1
  state.restoreToken = restoreToken
  let userMoved = false
  const markUserMoved = () => {
    userMoved = true
  }
  const cleanup = () => {
    window.removeEventListener('wheel', markUserMoved)
    window.removeEventListener('touchmove', markUserMoved)
    window.removeEventListener('keydown', markUserMoved)
    window.removeEventListener('pointerdown', markUserMoved)
  }
  const restore = () => window.scrollTo(left, top)
  const restoreUnlessUserMoved = () => {
    if (!userMoved && state.count === 0 && state.restoreToken === restoreToken) restore()
  }
  window.addEventListener('wheel', markUserMoved, { passive: true, once: true })
  window.addEventListener('touchmove', markUserMoved, { passive: true, once: true })
  window.addEventListener('keydown', markUserMoved, { once: true })
  window.addEventListener('pointerdown', markUserMoved, { passive: true, once: true })
  restoreUnlessUserMoved()
  const finishRestore = () => {
    restoreUnlessUserMoved()
    cleanup()
  }
  if (typeof window.requestAnimationFrame === 'function') {
    window.requestAnimationFrame(() => {
      restoreUnlessUserMoved()
      window.requestAnimationFrame(finishRestore)
    })
    return
  }
  finishRestore()
}

function unlockBodyScroll() {
  if (!ownsBodyLock || typeof document === 'undefined') return
  const state = modalLockState()
  if (state) {
    state.count = Math.max(0, state.count - 1)
    if (state.count === 0) {
      document.documentElement.classList.remove(MODAL_SCROLL_LOCK_CLASS)
      document.body.style.overflow = ''
      restoreBodyScroll(state)
    }
  }
  ownsBodyLock = false
}

function closeModal() {
  if (focusSession && !focusSession.isTop()) return
  emit('close')
}

function focusModalPanel() {
  if (typeof document === 'undefined') return
  nextTick(() => {
    const panel = panelRef.value
    if (!props.open || !panel) return
    if (focusSession) return
    focusSession = activateModalFocus(panel, effectiveLayer.value, opener)
  })
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      opener = typeof document !== 'undefined' ? document.activeElement : null
      lockBodyScroll()
      focusModalPanel()
    } else {
      focusSession?.release()
      focusSession = null
      unlockBodyScroll()
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => { focusSession?.release(); unlockBodyScroll() })
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="modal-backdrop"
      :style="{ zIndex: 300 + effectiveLayer }"
      data-testid="base-modal-backdrop"
      @click.self="closeModal"
      @keydown.esc.stop.prevent="closeModal"
    >
      <section
        ref="panelRef"
        class="modal-panel"
        :class="{ small: size === 'small', wide: size === 'wide' }"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="modalTitleId"
        tabindex="-1"
        data-testid="base-modal-panel"
      >
        <header class="modal-header">
          <div>
            <h3 :id="modalTitleId" class="card-title">{{ $t(title) }}</h3>
            <small class="helper-text">{{ currencyUnit }} · {{ areaUnit === 'ft2' ? 'ft²' : 'm²' }}<template v-if="fxDate"> · {{ $t('只读折算') }}</template></small>
          </div>
          <LanguageSwitch style="margin-left: auto; margin-right: 8px" />
          <button type="button" class="icon-btn" :aria-label="$t(&quot;关闭&quot;)" data-testid="base-modal-close" @click="closeModal">×</button>
        </header>
        <div class="modal-body">
          <slot />
        </div>
        <footer v-if="$slots.footer" class="modal-footer">
          <slot name="footer" />
        </footer>
      </section>
    </div>
  </Teleport>
</template>
