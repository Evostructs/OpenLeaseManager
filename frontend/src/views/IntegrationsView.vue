<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../lib/api'
import { formatDateTime } from '../lib/format'

const emit = defineEmits(['notify'])

const providers = ref([])
const connections = ref([])
const events = ref({ total: 0, items: [] })
const busy = ref(false)
const editingId = ref(null)

const form = reactive({
  name: '',
  provider: 'rest_webhook',
  direction: 'push',
  enabled: true,
  endpoint_url: '',
  bearer_token: '',
  remark: ''
})

const providerOptions = computed(() => providers.value.length ? providers.value : [
  { value: 'rest_webhook', label: 'REST Webhook', capabilities: ['push'] }
])

function notify(text, type = 'info') {
  emit('notify', { text, type })
}

function resetForm() {
  editingId.value = null
  form.name = ''
  form.provider = 'rest_webhook'
  form.direction = 'push'
  form.enabled = true
  form.endpoint_url = ''
  form.bearer_token = ''
  form.remark = ''
}

function fillForm(item) {
  editingId.value = item.id
  form.name = item.name
  form.provider = item.provider
  form.direction = item.direction
  form.enabled = item.enabled
  form.endpoint_url = item.config?.endpoint_url || ''
  form.bearer_token = ''
  form.remark = item.config?.remark || ''
}

async function loadProviders() {
  const result = await api('/api/integrations/providers')
  providers.value = result.providers || []
}

async function loadConnections() {
  connections.value = await api('/api/integrations')
}

async function loadEvents() {
  events.value = await api('/api/integrations/events?limit=80')
}

async function refreshAll() {
  await Promise.all([loadProviders(), loadConnections(), loadEvents()])
}

async function saveConnection() {
  busy.value = true
  try {
    const payload = {
      name: form.name,
      provider: form.provider,
      direction: form.direction,
      enabled: form.enabled,
      config: {
        endpoint_url: form.endpoint_url,
        remark: form.remark
      }
    }
    const secret = {}
    if (form.bearer_token) secret.bearer_token = form.bearer_token
    if (Object.keys(secret).length) payload.secret = secret

    if (editingId.value) {
      await api(`/api/integrations/${editingId.value}`, { method: 'PUT', body: JSON.stringify(payload) })
      notify('连接器已更新')
    } else {
      await api('/api/integrations', { method: 'POST', body: JSON.stringify(payload) })
      notify('连接器已创建')
    }
    resetForm()
    await refreshAll()
  } catch (error) {
    notify(error.message, 'error')
  } finally {
    busy.value = false
  }
}

async function deleteConnection(item) {
  if (!window.confirm(`确认删除连接器「${item.name}」？`)) return
  try {
    await api(`/api/integrations/${item.id}`, { method: 'DELETE' })
    notify('连接器已删除')
    await refreshAll()
  } catch (error) {
    notify(error.message, 'error')
  }
}

async function testConnection(item) {
  try {
    const result = await api(`/api/integrations/${item.id}/test`, { method: 'POST' })
    notify(result.message, result.success ? 'info' : 'error')
  } catch (error) {
    notify(error.message, 'error')
  }
}

async function syncConnection(item) {
  try {
    const result = await api(`/api/integrations/${item.id}/sync`, { method: 'POST' })
    notify(result.message, result.success ? 'info' : 'error')
    await loadEvents()
  } catch (error) {
    notify(error.message, 'error')
  }
}

async function retryEvent(item) {
  try {
    await api(`/api/integrations/events/${item.id}/retry`, { method: 'POST' })
    notify('事件已重试')
    await loadEvents()
  } catch (error) {
    notify(error.message, 'error')
  }
}

onMounted(refreshAll)
</script>

<template>
  <section class="content integrations-view">
    <div class="page-card">
      <div class="page-card-header">
        <div>
          <p class="section-label">开放集成</p>
          <h3>集成接口</h3>
          <p>预留 JDE、OA、财务系统、SaaS、Webhook 与 MCP 接入能力。当前可用的是 REST Webhook 推送。</p>
        </div>
      </div>

      <div class="integration-grid">
        <form class="card integration-form" data-testid="integration-form" @submit.prevent="saveConnection">
          <div class="card-header compact">
            <div>
              <p class="section-label">{{ editingId ? '编辑连接器' : '新增连接器' }}</p>
              <h4>{{ editingId ? '更新连接器' : '创建连接器' }}</h4>
            </div>
            <button v-if="editingId" type="button" class="btn compact-btn" @click="resetForm">取消编辑</button>
          </div>

          <label class="form-field">
            <span>名称</span>
            <input v-model.trim="form.name" class="input" required placeholder="例如：合同变更 Webhook" />
          </label>

          <div class="form-grid two">
            <label class="form-field">
              <span>类型</span>
              <select v-model="form.provider" class="select">
                <option v-for="item in providerOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
              </select>
            </label>
            <label class="form-field">
              <span>方向</span>
              <select v-model="form.direction" class="select">
                <option value="import">导入</option>
                <option value="push">推送</option>
                <option value="bidirectional">双向</option>
              </select>
            </label>
          </div>

          <label class="form-field">
            <span>Webhook 地址</span>
            <input v-model.trim="form.endpoint_url" class="input" placeholder="https://example.com/webhook" />
          </label>

          <label class="form-field">
            <span>Bearer Token / API Key</span>
            <input v-model.trim="form.bearer_token" class="input" type="password" placeholder="留空表示不修改密钥" />
            <small>密钥只加密保存，列表和详情不会回显明文。</small>
          </label>

          <label class="form-field">
            <span>备注</span>
            <input v-model.trim="form.remark" class="input" placeholder="用途、负责人或接入说明" />
          </label>

          <label class="switch-row">
            <input v-model="form.enabled" type="checkbox" />
            <span>启用连接器</span>
          </label>

          <button class="btn btn-primary" type="submit" :disabled="busy" data-testid="integrations-create">
            {{ busy ? '保存中' : editingId ? '保存修改' : '创建连接器' }}
          </button>
        </form>

        <div class="card">
          <div class="card-header compact">
            <div>
              <p class="section-label">连接器</p>
              <h4>连接器列表</h4>
            </div>
            <button class="btn compact-btn" type="button" @click="refreshAll">刷新</button>
          </div>
          <div class="table-wrapper" data-testid="integrations-table">
            <table class="data-table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>类型</th>
                  <th>方向</th>
                  <th>状态</th>
                  <th>密钥</th>
                  <th class="actions-cell">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in connections" :key="item.id">
                  <td>{{ item.name }}</td>
                  <td>{{ item.provider_label }}</td>
                  <td>{{ item.direction_label }}</td>
                  <td><span class="badge" :class="item.enabled ? 'success' : 'info'">{{ item.enabled ? '启用' : '停用' }}</span></td>
                  <td>{{ item.secret_configured ? '已配置' : '未配置' }}</td>
                  <td class="actions-cell">
                    <button class="icon-btn" title="编辑" @click="fillForm(item)">改</button>
                    <button class="icon-btn" title="测试" @click="testConnection(item)">测</button>
                    <button class="icon-btn" title="同步" @click="syncConnection(item)">同步</button>
                    <button class="icon-btn danger" title="删除" @click="deleteConnection(item)">删</button>
                  </td>
                </tr>
                <tr v-if="!connections.length">
                  <td colspan="6" class="list-empty">暂无连接器</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card integration-events-card">
        <div class="card-header compact">
          <div>
            <p class="section-label">Outbox</p>
            <h4>集成事件</h4>
          </div>
          <span class="badge info">共 {{ events.total || 0 }} 条</span>
        </div>
        <div class="table-wrapper" data-testid="integration-events-table">
          <table class="data-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>连接器</th>
                <th>事件</th>
                <th>对象</th>
                <th>状态</th>
                <th>重试</th>
                <th>错误</th>
                <th class="actions-cell">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in events.items" :key="item.id">
                <td>{{ formatDateTime(item.created_at) }}</td>
                <td>{{ item.connection_name || '--' }}</td>
                <td>{{ item.event_type }}</td>
                <td>{{ item.resource_type }} #{{ item.resource_id || '-' }}</td>
                <td><span class="badge" :class="{ success: item.status === 'sent', warning: item.status === 'pending', error: item.status === 'failed' }">{{ item.status }}</span></td>
                <td>{{ item.attempts }}</td>
                <td class="helper-text">{{ item.last_error || '--' }}</td>
                <td class="actions-cell">
                  <button class="icon-btn" :disabled="item.status === 'sent'" @click="retryEvent(item)">重试</button>
                </td>
              </tr>
              <tr v-if="!events.items?.length">
                <td colspan="8" class="list-empty">暂无事件</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>
