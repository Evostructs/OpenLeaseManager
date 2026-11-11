<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api, openDownload } from '../lib/api'
import { formatDateTime } from '../lib/format'
import BaseModal from './BaseModal.vue'

const props = defineProps({
  entityType: {
    type: String,
    required: true
  },
  entityId: {
    type: [Number, String],
    required: true
  },
  attachments: {
    type: Array,
    default: () => []
  },
  summary: {
    type: Object,
    default: () => ({})
  },
  canEdit: Boolean,
  canDelete: Boolean,
  requiredLabel: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['refresh', 'notify'])

const categories = ['合同正文', '补充协议', '证照', '押金凭证', '收款凭证', '沟通记录', '其他']
const uploadInput = ref(null)
const uploading = ref(false)
const editingId = ref(null)
const uploadForm = reactive({
  category: defaultCategory(),
  notes: ''
})
const editForm = reactive({
  category: '其他',
  notes: ''
})
const attachmentDeleteConfirm = reactive({ item: null, deleting: false })

const categoryCounts = computed(() => {
  const counts = props.summary?.categories || {}
  if (Object.keys(counts).length) return counts
  return props.attachments.reduce((result, item) => {
    const category = item?.category || '其他'
    result[category] = (result[category] || 0) + 1
    return result
  }, {})
})
const missingRequired = computed(() => {
  const missing = props.summary?.missing_required || []
  if (missing.length || !props.requiredLabel) return missing
  const requiredCategory = defaultCategory()
  return categoryCounts.value[requiredCategory] ? [] : [requiredCategory]
})
const totalAttachments = computed(() => {
  const value = Number(props.summary?.total)
  return Number.isFinite(value) ? value : props.attachments.length
})
const categoryEntries = computed(() =>
  Object.entries(categoryCounts.value)
    .filter(([, count]) => Number(count) > 0)
    .sort(([left], [right]) => categoryRank(left) - categoryRank(right) || left.localeCompare(right, 'zh-Hans-CN'))
)
const coveredCategoryCount = computed(() => categoryEntries.value.length)
const hasMissingRequired = computed(() => missingRequired.value.length > 0)
const missingRequiredText = computed(() => missingRequired.value.join('、') || props.requiredLabel)
const nextUploadCategory = computed(() => missingRequired.value[0] || defaultCategory())
const attachmentStatusTitle = computed(() => (hasMissingRequired.value ? `缺资料：${missingRequiredText.value}` : '资料已登记'))
const attachmentStatusText = computed(() =>
  hasMissingRequired.value
    ? `请补齐${missingRequiredText.value}，让当前记录形成完整证据链。`
    : '关键资料已覆盖，可继续补充押金、收款或沟通凭证。'
)

function categoryRank(category) {
  const index = categories.indexOf(category)
  return index === -1 ? categories.length : index
}

function defaultCategory() {
  if (props.entityType === 'contract') return '合同正文'
  if (props.entityType === 'customer') return '证照'
  return '其他'
}

function formatFileSize(size) {
  const value = Number(size || 0)
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  if (value >= 1024) return `${Math.ceil(value / 1024)} KB`
  return `${value} B`
}

function triggerUpload() {
  if (!props.canEdit || !props.entityId) return
  uploadInput.value?.click()
}

async function uploadAttachment(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !props.entityId) return

  const payload = new FormData()
  payload.append('entity_type', props.entityType)
  payload.append('entity_id', String(props.entityId))
  payload.append('category', uploadForm.category || '其他')
  payload.append('notes', uploadForm.notes || '')
  payload.append('file', file)

  uploading.value = true
  try {
    await api('/api/attachments', { method: 'POST', body: payload })
    uploadForm.notes = ''
    emit('notify', { text: '附件已上传' })
    emit('refresh')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    uploading.value = false
  }
}

function startEdit(item) {
  editingId.value = item.id
  editForm.category = item.category || '其他'
  editForm.notes = item.notes || ''
}

function cancelEdit() {
  editingId.value = null
}

async function saveEdit(item) {
  if (!item?.id) return
  try {
    await api(`/api/attachments/${item.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        category: editForm.category || '其他',
        notes: editForm.notes || ''
      })
    })
    editingId.value = null
    emit('notify', { text: '附件信息已更新' })
    emit('refresh')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

function removeAttachment(item) {
  if (!props.canDelete) return
  if (!item?.id) return
  attachmentDeleteConfirm.item = item
}

function closeRemoveAttachmentConfirm() {
  if (attachmentDeleteConfirm.deleting) return
  attachmentDeleteConfirm.item = null
}

async function confirmRemoveAttachment() {
  if (attachmentDeleteConfirm.deleting) return
  if (!props.canDelete) return
  const item = attachmentDeleteConfirm.item
  if (!item?.id) return
  attachmentDeleteConfirm.deleting = true
  try {
    await api(`/api/attachments/${item.id}`, { method: 'DELETE' })
    emit('notify', { text: '附件已删除' })
    emit('refresh')
    attachmentDeleteConfirm.item = null
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    attachmentDeleteConfirm.deleting = false
  }
}

watch(
  () => props.entityType,
  () => {
    uploadForm.category = defaultCategory()
  }
)
</script>

<template>
  <section class="attachment-panel" data-testid="attachment-panel">
    <div class="attachment-status" :class="{ 'is-missing': hasMissingRequired }" data-testid="attachment-status">
      <div class="attachment-status-main">
        <span class="attachment-status-label">附件证据链</span>
        <strong>{{ attachmentStatusTitle }}</strong>
        <p>{{ attachmentStatusText }}</p>
      </div>
      <div class="attachment-summary-line" data-testid="attachment-summary">
        <span class="attachment-summary-pill">
          <b>{{ totalAttachments }}</b>
          <small>文件</small>
        </span>
        <span class="attachment-summary-pill">
          <b>{{ coveredCategoryCount }}</b>
          <small>类别</small>
        </span>
        <span v-if="hasMissingRequired" class="attachment-summary-gap">待补 {{ missingRequiredText }}</span>
        <span v-else class="attachment-summary-gap is-complete">覆盖完整</span>
      </div>
    </div>

    <div v-if="categoryEntries.length" class="attachment-category-strip">
      <span v-for="[category, count] in categoryEntries" :key="category" class="attachment-category-chip">
        {{ category }} {{ count }}
      </span>
    </div>

    <div v-if="canEdit" class="attachment-upload-row" data-testid="attachment-upload-row">
      <label class="attachment-upload-field">
        <span>资料类别</span>
        <select v-model="uploadForm.category" class="select">
          <option v-for="category in categories" :key="category" :value="category">{{ category }}</option>
        </select>
      </label>
      <label class="attachment-upload-field">
        <span>证据说明</span>
        <input v-model="uploadForm.notes" class="input" placeholder="填写这份资料能证明什么" />
      </label>
      <button class="btn btn-primary" :disabled="uploading" type="button" @click="triggerUpload">
        {{ uploading ? '上传中' : '上传附件' }}
      </button>
      <input ref="uploadInput" class="visually-hidden" type="file" @change="uploadAttachment" />
    </div>

    <div v-if="attachments.length" class="attachment-list" data-testid="attachment-list">
      <div
        v-for="item in attachments"
        :key="item.id"
        class="attachment-item"
        :class="{ 'attachment-item-editing': editingId === item.id }"
        :data-testid="`attachment-item-${item.id}`"
      >
        <template v-if="editingId === item.id">
          <div class="attachment-edit-fields">
            <label class="attachment-upload-field">
              <span>资料类别</span>
              <select v-model="editForm.category" class="select">
                <option v-for="category in categories" :key="category" :value="category">{{ category }}</option>
              </select>
            </label>
            <label class="attachment-upload-field">
              <span>证据说明</span>
              <input v-model="editForm.notes" class="input" placeholder="填写这份资料能证明什么" />
            </label>
          </div>
          <div class="attachment-actions">
            <button class="btn btn-compact btn-primary" type="button" @click="saveEdit(item)">保存</button>
            <button class="btn btn-compact" type="button" @click="cancelEdit">取消</button>
          </div>
        </template>
        <template v-else>
          <div class="attachment-evidence">
            <span class="attachment-category">{{ item.category || '其他' }}</span>
            <div class="attachment-meta">
              <strong class="attachment-file-name" :title="item.file_name">{{ item.file_name }}</strong>
              <div class="attachment-facts">
                <span>{{ formatFileSize(item.file_size) }}</span>
                <span>{{ formatDateTime(item.created_at) }}</span>
                <span>上传人 {{ item.uploaded_by || '--' }}</span>
              </div>
              <p class="attachment-notes" :class="{ 'is-empty': !item.notes }">
                <span>备注</span>
                {{ item.notes || '未填写' }}
              </p>
            </div>
          </div>
          <div class="attachment-actions">
            <button class="btn btn-compact" type="button" @click="openDownload(`/api/attachments/${item.id}/download`)">打开</button>
            <button v-if="canEdit" class="btn btn-compact" type="button" @click="startEdit(item)">编辑</button>
            <button
              v-if="canDelete"
              class="btn btn-compact btn-danger"
              type="button"
              :data-testid="`attachment-delete-action-${item.id}`"
              @click="removeAttachment(item)"
            >
              删除
            </button>
          </div>
        </template>
      </div>
    </div>
    <div v-else class="attachment-empty compact-empty">
      <strong>{{ hasMissingRequired ? `当前缺少${missingRequiredText}` : '还没有登记附件' }}</strong>
      <p>
        {{
          hasMissingRequired
            ? `下一步建议上传${nextUploadCategory}，补齐这条业务记录的关键凭证。`
            : `下一步建议上传${nextUploadCategory}，先建立可追溯资料。`
        }}
      </p>
    </div>
    <BaseModal :open="Boolean(attachmentDeleteConfirm.item)" title="删除附件" size="small" @close="closeRemoveAttachmentConfirm">
      <div data-testid="attachment-delete-confirm-modal" class="detail-list">
        <p>确定删除附件 {{ attachmentDeleteConfirm.item?.file_name || '--' }} 吗？</p>
        <p class="helper-text">删除后该附件将从当前业务记录中移除，无法直接恢复。</p>
      </div>
      <template #footer>
        <button
          class="btn"
          data-testid="attachment-delete-cancel"
          type="button"
          :disabled="attachmentDeleteConfirm.deleting"
          @click="closeRemoveAttachmentConfirm"
        >
          取消
        </button>
        <button
          class="btn btn-danger"
          data-testid="attachment-delete-confirm"
          type="button"
          :disabled="attachmentDeleteConfirm.deleting"
          @click="confirmRemoveAttachment"
        >
          {{ attachmentDeleteConfirm.deleting ? '删除中' : '确认删除' }}
        </button>
      </template>
    </BaseModal>
  </section>
</template>
