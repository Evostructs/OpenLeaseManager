<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../lib/api'
import { buildViewLink } from '../lib/savedViews'
import { getPropertyScopeContext } from '../lib/propertyScope'
import ContractDetailModal from './ContractDetailModal.vue'

const props = defineProps({
  open: Boolean,
  contractId: {
    type: [Number, String],
    default: null
  },
  user: {
    type: Object,
    required: true
  },
  showActions: {
    type: Boolean,
    default: false
  },
  initialSection: {
    type: String,
    default: ''
  },
  closeLabel: {
    type: String,
    default: '关闭'
  },
  layerIndex: {
    type: Number,
    default: 0
  }
})

const detailLink = computed(() => {
  const scope = getPropertyScopeContext()
  return buildViewLink(window.location.origin, 'contracts', { type: scope.scopeType, code: scope.scopeCode }, {}, { type: 'contract', id: props.contractId })
})

const emit = defineEmits(['close', 'notify', 'open-contract', 'open-customer'])

const canEdit = computed(() => props.user?.role === 'admin')
const showLogs = computed(() => Boolean(props.showActions && canEdit.value))
const detail = ref(null)
const detailLoading = ref(false)
const detailLogs = ref([])
const detailPlan = ref([])
const detailPreview = ref([])
const detailSummary = ref({})
const decisionCard = ref(null)
const decisionLoading = ref(false)
const decisionError = ref('')
let requestId = 0

function resetDetail() {
  detail.value = null
  detailLogs.value = []
  detailPlan.value = []
  detailPreview.value = []
  detailSummary.value = {}
  decisionCard.value = null
  decisionError.value = ''
}

async function refreshContractDetail(contractId, options = {}) {
  const id = typeof contractId === 'object' ? contractId?.id : contractId
  if (!id) return
  const currentRequest = ++requestId
  if (options.reset) resetDetail()
  detailLoading.value = true
  decisionLoading.value = true

  const decisionRequest = api(`/api/contracts/${id}/decision-card`)
    .then((card) => {
      if (currentRequest === requestId) {
        decisionCard.value = card
        decisionError.value = ''
      }
    })
    .catch((error) => {
      if (currentRequest === requestId) {
        decisionCard.value = null
        decisionError.value = error.message
      }
    })
    .finally(() => {
      if (currentRequest === requestId) decisionLoading.value = false
    })

  try {
    const detailRequest = api(`/api/contracts/${id}`)
    const planRequest = api(`/api/contracts/${id}/rent-plan`)
    const logsRequest = showLogs.value ? api(`/api/contracts/${id}/logs`) : Promise.resolve([])
    const [contract, plan, logs] = await Promise.all([detailRequest, planRequest, logsRequest])
    if (currentRequest !== requestId) return
    detail.value = contract
    detailPlan.value = plan.plan || []
    detailPreview.value = plan.preview || []
    detailSummary.value = plan.summary || {}
    detailLogs.value = logs || []
  } catch (error) {
    if (currentRequest === requestId) {
      if (options.closeOnError) emit('close')
      emit('notify', { type: 'error', text: error.message })
    }
  } finally {
    if (currentRequest === requestId) detailLoading.value = false
    await decisionRequest
  }
}

function refreshCurrent() {
  if (detail.value?.id) refreshContractDetail(detail.value.id)
}

function openContract(target) {
  const id = typeof target === 'object' ? (target?.id || target?.contract_id) : target
  if (id) emit('open-contract', typeof target === 'object' ? { ...target, id } : id)
}

watch(
  () => [props.open, props.contractId],
  ([open, contractId]) => {
    if (open && contractId) {
      refreshContractDetail(contractId, { reset: true, closeOnError: true })
    } else if (!open) {
      resetDetail()
    }
  },
  { immediate: true }
)
</script>

<template>
  <ContractDetailModal
    :open="open"
    :contract="detail"
    :detail-link="detailLink"
    :loading="detailLoading"
    :logs="detailLogs"
    :rent-plan="detailPlan"
    :rent-preview="detailPreview"
    :rent-summary="detailSummary"
    :decision-card="decisionCard"
    :decision-loading="decisionLoading"
    :decision-error="decisionError"
    :initial-section="initialSection"
    :show-logs="showLogs"
    :close-label="closeLabel"
    :show-close-action="true"
    :layer-index="layerIndex"
    @close="emit('close')"
    @open-contract="openContract"
    @open-customer="emit('open-customer', $event)"
    @refresh="refreshCurrent"
    @notify="emit('notify', $event)"
    @refresh-decision-card="refreshCurrent"
  />
</template>
