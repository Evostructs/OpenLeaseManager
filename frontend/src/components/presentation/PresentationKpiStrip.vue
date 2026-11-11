<script setup>
import AnimatedNumber from '../AnimatedNumber.vue'

const props = defineProps({
  cards: {
    type: Array,
    default: () => []
  },
  layout: {
    type: String,
    default: 'single'
  },
  interactive: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['navigate'])

function openCard(card) {
  if (!props.interactive || !card?.target) return
  emit('navigate', { tab: card.target, metric: card.key })
}
</script>

<template>
  <section class="cockpit-kpis" :class="`is-${props.layout}`" :aria-label="$t(&quot;核心经营指标&quot;)">
    <button
      v-for="card in cards"
      :key="card.key"
      type="button"
      class="cockpit-kpi"
      :class="[card.tone, { actionable: props.interactive && card.target, unavailable: card.available === false }]"
      :disabled="!props.interactive || !card.target"
      :data-testid="`presentation-kpi-${card.key}`"
      @click="openCard(card)"
    >
      <span class="cockpit-kpi-label">{{ $t(card.label) }}</span>
      <AnimatedNumber :value="card.value" />
      <span v-if="card.hint" class="cockpit-kpi-hint">{{ $t(card.hint) }}</span>
    </button>
  </section>
</template>

<style scoped>
.cockpit-kpis {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 16px;
  flex: 0 0 auto;
}

.cockpit-kpis.is-portfolio {
  grid-template-columns: repeat(9, minmax(0, 1fr));
  gap: 10px;
}

.cockpit-kpi {
  appearance: none;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px 22px;
  border-radius: 14px;
  background: rgba(17, 29, 51, 0.72);
  border: 1px solid rgba(90, 140, 255, 0.16);
  border-top: 3px solid transparent;
  color: #e8eefc;
  font: inherit;
  text-align: left;
}

.cockpit-kpis.is-portfolio .cockpit-kpi {
  gap: 7px;
  padding: 14px 13px 12px;
  border-radius: 12px;
  background: linear-gradient(155deg, rgba(20, 36, 64, 0.86), rgba(10, 21, 41, 0.76));
}

.cockpit-kpi.actionable {
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.cockpit-kpi.actionable:hover,
.cockpit-kpi.actionable:focus-visible {
  transform: translateY(-2px);
  border-color: rgba(125, 211, 252, 0.48);
  background: rgba(25, 43, 74, 0.88);
  outline: none;
}

.cockpit-kpi:disabled {
  opacity: 1;
}

.cockpit-kpi.unavailable :deep(.animated-number) {
  color: #7183a5;
}

.cockpit-kpi-label {
  overflow: hidden;
  color: #8ea2c9;
  font-size: 15px;
  letter-spacing: 1px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cockpit-kpi :deep(.animated-number) {
  overflow: hidden;
  color: #e8eefc;
  font-size: clamp(21px, 1.9vw, 36px);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cockpit-kpis.is-portfolio .cockpit-kpi-label {
  font-size: 12px;
}

.cockpit-kpis.is-portfolio .cockpit-kpi :deep(.animated-number) {
  font-size: clamp(16px, 1.15vw, 23px);
}

.cockpit-kpi-hint {
  overflow: hidden;
  color: #61759b;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cockpit-kpi.cyan { border-top-color: #38bdf8; }
.cockpit-kpi.cyan :deep(.animated-number) { color: #7dd3fc; }
.cockpit-kpi.green { border-top-color: #34d399; }
.cockpit-kpi.green :deep(.animated-number) { color: #6ee7b7; }
.cockpit-kpi.blue { border-top-color: #60a5fa; }
.cockpit-kpi.blue :deep(.animated-number) { color: #93c5fd; }
.cockpit-kpi.red { border-top-color: #f87171; }
.cockpit-kpi.red :deep(.animated-number) { color: #fca5a5; }
.cockpit-kpi.amber { border-top-color: #fbbf24; }
.cockpit-kpi.amber :deep(.animated-number) { color: #fcd34d; }

@media (max-width: 1600px) {
  .cockpit-kpis.is-portfolio {
    grid-template-columns: repeat(5, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .cockpit-kpis,
  .cockpit-kpis.is-portfolio {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }

  .cockpit-kpi,
  .cockpit-kpis.is-portfolio .cockpit-kpi {
    min-height: 84px;
    gap: 6px;
    padding: 11px 12px 10px;
    border-radius: 10px;
  }

  .cockpit-kpi-label,
  .cockpit-kpis.is-portfolio .cockpit-kpi-label {
    overflow: visible;
    font-size: 12px;
    line-height: 1.35;
    text-overflow: clip;
    white-space: normal;
  }

  .cockpit-kpi :deep(.animated-number),
  .cockpit-kpis.is-portfolio .cockpit-kpi :deep(.animated-number) {
    overflow: visible;
    font-size: 20px;
    text-overflow: clip;
  }

  .cockpit-kpi-hint {
    overflow: visible;
    font-size: 9px;
    line-height: 1.35;
    text-overflow: clip;
    white-space: normal;
  }

  .cockpit-kpis.is-portfolio .cockpit-kpi:last-child:nth-child(odd) {
    grid-column: 1 / -1;
  }
}
</style>
