<script setup>
import { onMounted, ref, watch } from 'vue'

const props = defineProps({
  value: {
    type: [String, Number],
    required: true
  }
})

// 从 "¥12,345" / "86%" / "12 天" 这类串中分离 前缀/数值/后缀，动画只滚数值部分；
// 纯文本内容直接显示，不做动画。
const display = ref('')
let raf = 0
let current = 0

function splitParts(raw) {
  const text = String(raw ?? '')
  const match = text.match(/-?[\d,]+(?:\.\d+)?/)
  if (!match) return null
  const number = Number(match[0].replace(/,/g, ''))
  if (!Number.isFinite(number)) return null
  const decimals = (match[0].split('.')[1] || '').length
  return {
    prefix: text.slice(0, match.index),
    suffix: text.slice(match.index + match[0].length),
    number,
    decimals
  }
}

function render(parts, value) {
  const formatted = value.toLocaleString('zh-CN', {
    minimumFractionDigits: parts.decimals,
    maximumFractionDigits: parts.decimals
  })
  display.value = `${parts.prefix}${formatted}${parts.suffix}`
}

function animate(raw) {
  cancelAnimationFrame(raf)
  const parts = splitParts(raw)
  if (!parts) {
    current = 0
    display.value = String(raw ?? '')
    return
  }
  const from = current
  const to = parts.number
  // 页面不可见（后台标签页/息屏）时 rAF 暂停，动画永不推进会导致数字空白；
  // 此时直接渲染终值，页面重新可见时值已正确
  if (from === to || document.hidden) {
    current = to
    render(parts, to)
    return
  }
  const start = performance.now()
  const duration = 700
  const tick = (now) => {
    const t = Math.min(1, (now - start) / duration)
    const eased = 1 - Math.pow(1 - t, 3)
    render(parts, from + (to - from) * eased)
    if (t < 1) {
      raf = requestAnimationFrame(tick)
    } else {
      current = to
      render(parts, to)
    }
  }
  raf = requestAnimationFrame(tick)
}

onMounted(() => animate(props.value))
watch(() => props.value, (value) => animate(value))
</script>

<template>
  <strong class="animated-number">{{ $t(display) }}</strong>
</template>
