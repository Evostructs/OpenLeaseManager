<script setup>
import { computed } from 'vue'
import PresentationView from '../components/PresentationView.vue'
import {
  buildLeadershipPresentationState,
  selectPortfolioOverviewSnapshot
} from '../lib/presentationDomain'

const props = defineProps({
  snapshot: {
    type: Object,
    default: () => ({})
  },
  buildingCode: {
    type: String,
    default: ''
  },
  loading: {
    type: Boolean,
    default: false
  },
  error: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['select-building', 'back-overview', 'refresh'])

const hasSnapshot = computed(() => props.snapshot?.contract_version === 'portfolio-overview-v1')
const displaySnapshot = computed(() => (
  selectPortfolioOverviewSnapshot(props.snapshot, props.buildingCode)
))
const presentationState = computed(() => buildLeadershipPresentationState(displaySnapshot.value))
</script>

<template>
  <section class="leadership-overview-page" data-testid="leadership-overview-page">
    <div
      v-if="loading && !hasSnapshot"
      class="leadership-overview-state"
      data-testid="leadership-overview-loading"
    >
      <strong>{{ $t("正在读取板块概况") }}</strong>
      <span>{{ $t("正在加载经营概况…") }}</span>
    </div>

    <div
      v-else-if="error && !hasSnapshot"
      class="leadership-overview-state is-error"
      data-testid="leadership-overview-error"
    >
      <strong>{{ $t("板块概况暂不可用") }}</strong>
      <span>{{ $t(error) }}</span>
      <button type="button" class="btn" @click="emit('refresh')">{{ $t("重新读取") }}</button>
    </div>

    <PresentationView
      v-else-if="hasSnapshot"
      :snapshot="displaySnapshot"
      :stats="presentationState.stats"
      :operations="presentationState.operations"
      overview-only
      embedded
      @select-building="emit('select-building', $event)"
      @back-overview="emit('back-overview')"
    />

    <div v-else class="leadership-overview-state" data-testid="leadership-overview-empty">
      <strong>{{ $t("当前没有可展示的板块概况") }}</strong>
      <span>{{ $t("请联系平台管理员核对概况授权。") }}</span>
    </div>
  </section>
</template>

<style scoped>
.leadership-overview-page {
  min-width: 0;
  min-height: calc(100vh - 60px);
  background: #07101f;
}

.leadership-overview-state {
  min-height: calc(100vh - 60px);
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 10px;
  padding: 32px;
  background: var(--surface);
  color: var(--text-muted);
  text-align: center;
}

.leadership-overview-state strong {
  color: var(--text);
  font-size: 20px;
}

.leadership-overview-state.is-error strong {
  color: var(--danger-text);
}
</style>
