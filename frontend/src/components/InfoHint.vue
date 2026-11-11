<script setup>
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'

const props = defineProps({
  text: {
    type: String,
    required: true
  },
  label: {
    type: String,
    default: '查看说明'
  },
  align: {
    type: String,
    default: 'right'
  }
})

const tooltipId = useId()
const rootRef = ref(null)
const buttonRef = ref(null)
const popoverRef = ref(null)
const isOpen = ref(false)
const popoverStyle = ref({})

let wasOpenOnPointerDown = null

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max)
}

function updatePopoverPosition() {
  if (typeof window === 'undefined' || !buttonRef.value || !popoverRef.value) {
    return
  }

  // 注：标准化 CSS zoom（Chrome 126+ / Firefox 126+）下 getBoundingClientRect、
  // documentElement.clientWidth 与 fixed 定位的 left/top 同属一套逻辑坐标系，
  // 此处无需也不应再按 body zoom 换算（base-widescreen.css）。
  const margin = 12
  const gap = 8
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight
  const width = Math.max(120, Math.min(300, viewportWidth - margin * 2))
  const buttonRect = buttonRef.value.getBoundingClientRect()
  const popoverRect = popoverRef.value.getBoundingClientRect()
  const height = Math.min(popoverRect.height || popoverRef.value.scrollHeight, viewportHeight - margin * 2)
  const rawLeft = props.align === 'left' ? buttonRect.left : buttonRect.right - width
  const maxLeft = Math.max(margin, viewportWidth - width - margin)
  const left = clamp(rawLeft, margin, maxLeft)
  const belowTop = buttonRect.bottom + gap
  const aboveTop = buttonRect.top - height - gap
  const hasRoomBelow = belowTop + height <= viewportHeight - margin
  const rawTop = hasRoomBelow || aboveTop < margin ? belowTop : aboveTop
  const top = clamp(rawTop, margin, Math.max(margin, viewportHeight - height - margin))

  popoverStyle.value = {
    '--info-hint-left': `${Math.round(left)}px`,
    '--info-hint-top': `${Math.round(top)}px`
  }
}

async function openTooltip() {
  if (isOpen.value) {
    updatePopoverPosition()
    return
  }
  isOpen.value = true
  await nextTick()
  updatePopoverPosition()
}

function closeTooltip() {
  isOpen.value = false
}

function closeTooltipIfUnfocused() {
  if (typeof document !== 'undefined' && rootRef.value?.contains(document.activeElement)) {
    return
  }
  closeTooltip()
}

function handleButtonPointerDown() {
  wasOpenOnPointerDown = isOpen.value
}

function handleButtonClick() {
  if (wasOpenOnPointerDown === null) {
    isOpen.value ? closeTooltip() : openTooltip()
  } else if (wasOpenOnPointerDown) {
    closeTooltip()
  } else {
    openTooltip()
  }
  wasOpenOnPointerDown = null
}

function handleFocusOut(event) {
  const nextTarget = event.relatedTarget
  if (nextTarget instanceof Node && rootRef.value?.contains(nextTarget)) {
    return
  }
  closeTooltip()
}

function handleDocumentPointerDown(event) {
  if (rootRef.value?.contains(event.target)) {
    return
  }
  closeTooltip()
}

function addGlobalListeners() {
  if (typeof window === 'undefined') {
    return
  }
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  window.addEventListener('resize', updatePopoverPosition)
  window.addEventListener('scroll', updatePopoverPosition, true)
}

function removeGlobalListeners() {
  if (typeof window === 'undefined') {
    return
  }
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  window.removeEventListener('resize', updatePopoverPosition)
  window.removeEventListener('scroll', updatePopoverPosition, true)
}

watch(isOpen, (open) => {
  if (open) {
    addGlobalListeners()
    nextTick(updatePopoverPosition)
  } else {
    removeGlobalListeners()
  }
})

onBeforeUnmount(removeGlobalListeners)
</script>

<template>
  <span
    ref="rootRef"
    class="info-hint"
    :class="[`align-${align}`, { 'is-open': isOpen }]"
    @focusout="handleFocusOut"
    @keydown.esc.stop.prevent="closeTooltip"
    @mouseenter="openTooltip"
    @mouseleave="closeTooltipIfUnfocused"
  >
    <button
      ref="buttonRef"
      type="button"
      class="info-hint-button"
      :aria-label="$t(label)"
      :aria-describedby="tooltipId"
      :aria-expanded="isOpen ? 'true' : 'false'"
      @focus="openTooltip"
      @pointerdown="handleButtonPointerDown"
      @click.stop="handleButtonClick"
    >
      ?
    </button>
    <!-- teleport 到 body：卡片等容器的 backdrop-filter 会为 fixed 后代创建包含块，
         导致弹层被锁进容器坐标系而错位/溢出视口；层级用 --z-tooltip 保证盖过弹窗 -->
    <Teleport to="body">
      <span
        :id="tooltipId"
        ref="popoverRef"
        class="info-hint-popover"
        :class="[`align-${align}`, { 'is-open': isOpen }]"
        role="tooltip"
        :style="popoverStyle"
      >
        {{ $t(text) }}
      </span>
    </Teleport>
  </span>
</template>

<style scoped>
.info-hint {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  vertical-align: middle;
}

.info-hint-button {
  width: 24px;
  height: 24px;
  border: 1px solid rgba(148, 163, 184, 0.48);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.86);
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 720;
  line-height: 1;
  box-shadow: var(--shadow-sm);
  cursor: help;
}

.info-hint-button:hover,
.info-hint-button:focus-visible,
.info-hint.is-open .info-hint-button {
  border-color: rgba(59, 130, 246, 0.58);
  background: var(--info-bg);
  color: var(--info-text);
  outline: none;
}

.info-hint-popover {
  position: fixed;
  top: var(--info-hint-top, 0);
  left: var(--info-hint-left, 0);
  z-index: var(--z-tooltip);
  width: min(300px, calc(100vw - 24px));
  max-width: calc(100vw - 24px);
  max-height: min(220px, calc(100vh - 24px));
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid rgba(203, 213, 225, 0.88);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.98);
  color: var(--text);
  font-size: 12px;
  line-height: 1.55;
  text-align: left;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  box-shadow: 0 18px 36px rgba(15, 23, 42, 0.14);
  opacity: 0;
  visibility: hidden;
  transform: translateY(-4px);
  transform-origin: top right;
  transition: opacity 0.14s ease, transform 0.14s ease, visibility 0.14s ease;
  pointer-events: none;
}

/* teleport 后 popover 不再是 .info-hint 的后代，
   可见性与对齐类必须绑在 popover 自身（scoped 选择器不依赖祖孙关系） */
.info-hint-popover.align-left {
  transform-origin: top left;
}

.info-hint-popover.is-open {
  opacity: 1;
  visibility: visible;
  transform: translateY(0);
  pointer-events: auto;
}

@media (prefers-reduced-motion: reduce) {
  .info-hint-popover {
    transition: none;
  }
}
</style>
