<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { api, openDownload } from '../lib/api'
import { formatArea } from '../lib/format'
import { sortTableRows } from '../lib/table'
import RoomFormModal from '../components/RoomFormModal.vue'

const props = defineProps({
  user: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['notify'])
const canEdit = computed(() => ['admin', 'editor'].includes(props.user?.role))

const rooms = ref([])
const stats = ref(null)
const roomsLoading = ref(false)
const selectedRoomIds = ref([])
const filters = reactive({ occupancy: '', tower: '', use: '', subtype: '' })
const roomSort = reactive({
  key: '',
  direction: ''
})
const roomFormOpen = ref(false)
const editingRoom = ref(null)
const importInput = ref(null)
let roomFilterScrollTimer = null

function normalizeText(value) {
  return String(value ?? '').trim()
}

function normalizeRoom(room) {
  return {
    ...room,
    tower: normalizeText(room.tower),
    floor: normalizeText(room.floor),
    room_no: normalizeText(room.room_no),
    room_type: normalizeText(room.room_type),
    notes: normalizeText(room.notes),
    contract: room.contract || null
  }
}

function parseFloorNumber(value) {
  const raw = normalizeText(value).toUpperCase()
  if (!raw) return null
  if (raw.startsWith('B')) return -Number(raw.slice(1) || 0)
  const number = Number(raw)
  return Number.isNaN(number) ? null : number
}

function getUseCategory(room) {
  return room.room_type === '办公' ? 'office' : 'retail'
}

function getRoomStatus(room) {
  if (!room.occupied) return '空置'
  const contractStatus = normalizeText(room.contract?.status)
  return contractStatus || '已出租'
}

function getRoomStatusTone(status) {
  if (status === '空置') return 'info'
  if (status === '即将到期' || status === '逾期占用') return 'warning'
  if (status === '已退租' || status === '已到期') return 'error'
  return 'success'
}

function getBusinessSubtype(room) {
  if (room.room_type && room.room_type !== '办公') return room.room_type
  return ''
}

const filteredRooms = computed(() =>
  rooms.value.filter((room) => {
    if (filters.occupancy && getRoomStatus(room) !== filters.occupancy) return false
    if (filters.tower && room.tower !== filters.tower) return false
    if (filters.use && getUseCategory(room) !== filters.use) return false
    if (filters.subtype && getBusinessSubtype(room) !== filters.subtype) return false
    return true
  })
)

const sortedRooms = computed(() => {
  if (!roomSort.key || !roomSort.direction) return filteredRooms.value

  const accessors = {
    room_no: (room) => room.room_no,
    tower: (room) => room.tower,
    floor: (room) => parseFloorNumber(room.floor) ?? room.floor,
    room_type: (room) => room.room_type,
    area: (room) => room.area,
    occupancy: (room) => getRoomStatus(room),
    tenant_name: (room) => room.contract?.tenant_name || ''
  }

  return sortTableRows(filteredRooms.value, accessors[roomSort.key], roomSort.direction)
})

const towerOptions = computed(() => [...new Set(rooms.value.map((room) => room.tower).filter(Boolean))])

const subtypeOptions = computed(() => {
  const options = new Set()
  for (const room of rooms.value) {
    const subtype = getBusinessSubtype(room)
    if (subtype) options.add(subtype)
  }
  return [...options]
})

const groupedHeatmap = computed(() => {
  const parseFloorOrder = (value) => {
    if (!value) return -999
    if (String(value).startsWith('B')) return -Number(String(value).slice(1) || 0)
    return Number(value)
  }

  const buckets = {}
  for (const room of sortedRooms.value) {
    const tower = room.tower || '未标注楼栋'
    const floor = room.floor || '0'
    buckets[tower] ||= {}
    buckets[tower][floor] ||= []
    const labelStatus = getRoomStatus(room)
    const tone = getRoomStatusTone(labelStatus)

    buckets[tower][floor].push({
      ...room,
      labelStatus,
      tone
    })
  }

  return Object.fromEntries(
    Object.entries(buckets).map(([tower, floors]) => [
      tower,
      Object.entries(floors)
        .sort((a, b) => parseFloorOrder(b[0]) - parseFloorOrder(a[0]))
        .map(([floor, items]) => ({ floor, items }))
    ])
  )
})

async function fetchRooms() {
  roomsLoading.value = true
  try {
    const [roomList, roomStats] = await Promise.all([api('/api/rooms'), api('/api/rooms/stats')])
    rooms.value = roomList.map(normalizeRoom)
    stats.value = roomStats
    selectedRoomIds.value = []
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    roomsLoading.value = false
  }
}

async function saveRoom(payload) {
  try {
    if (editingRoom.value?.id) {
      await api(`/api/rooms/${editingRoom.value.id}`, { method: 'PUT', body: JSON.stringify(payload) })
      emit('notify', { text: '房间更新成功' })
    } else {
      await api('/api/rooms', { method: 'POST', body: JSON.stringify(payload) })
      emit('notify', { text: '房间创建成功' })
    }
    roomFormOpen.value = false
    editingRoom.value = null
    await fetchRooms()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function importRooms(event) {
  const file = event.target.files?.[0]
  if (!file) return

  const payload = new FormData()
  payload.append('file', file)

  try {
    const result = await api('/api/rooms/import', { method: 'POST', body: payload })
    emit('notify', { text: `房间导入成功：新增 ${result.added} 间` })
    await fetchRooms()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    event.target.value = ''
  }
}

async function deleteRoom(row) {
  if (!window.confirm(`确定删除房间 ${row.room_no} 吗？`)) return
  try {
    await api(`/api/rooms/${row.id}`, { method: 'DELETE' })
    emit('notify', { text: '房间已删除' })
    await fetchRooms()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

function triggerImport() {
  importInput.value?.click()
}

function onUseCategoryChange() {
  if (filters.use !== 'retail') {
    filters.subtype = ''
  }
  preserveViewportAfterFilterChange()
}

function preserveViewportAfterFilterChange() {
  const stableY = window.scrollY
  window.clearTimeout(roomFilterScrollTimer)
  nextTick(() => {
    window.scrollTo({ top: stableY })
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: stableY })
    })
    roomFilterScrollTimer = window.setTimeout(() => {
      window.scrollTo({ top: stableY })
    }, 120)
  })
}

function subtypeLabel(value) {
  return value || '其他商业'
}

function toggleRoomSort(key) {
  if (roomSort.key !== key) {
    roomSort.key = key
    roomSort.direction = 'asc'
    return
  }

  if (roomSort.direction === 'asc') {
    roomSort.direction = 'desc'
    return
  }

  if (roomSort.direction === 'desc') {
    roomSort.key = ''
    roomSort.direction = ''
    return
  }

  roomSort.direction = 'asc'
}

function roomSortIndicator(key) {
  if (roomSort.key !== key) return '↕'
  return roomSort.direction === 'asc' ? '↑' : '↓'
}

function toggleAllRooms(checked) {
  selectedRoomIds.value = checked ? sortedRooms.value.map((room) => room.id) : []
}

onMounted(fetchRooms)
</script>

<template>
  <section class="content">
    <article class="card room-dynamic-panel">
      <div class="card-header compact">
        <div>
          <div class="section-label">房态分布</div>
          <h3 class="card-title">房间热力图</h3>
        </div>
        <div class="legend-inline">
          <span class="legend-chip success">在租 / 稳定</span>
          <span class="legend-chip warning">即将到期 / 异常</span>
          <span class="legend-chip info">空置</span>
        </div>
      </div>

      <div v-if="roomsLoading" class="compact-empty">正在加载房间数据</div>
      <div v-else-if="!sortedRooms.length" class="compact-empty">当前筛选条件下没有房间</div>
      <div v-else class="room-heatmap-grid">
        <section v-for="(floors, tower) in groupedHeatmap" :key="tower" class="tower-card">
          <div class="tower-card-header">
            <strong>{{ tower }}</strong>
            <span>{{ floors.reduce((count, floor) => count + floor.items.length, 0) }} 间</span>
          </div>
          <div class="heatmap-board">
            <div v-for="floor in floors" :key="`${tower}-${floor.floor}`" class="heatmap-floor">
              <strong>{{ floor.floor }}F</strong>
              <div class="heatmap-rooms">
                <div v-for="room in floor.items" :key="room.id" class="heatmap-room" :class="room.tone">
                  <span>{{ room.room_no }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </article>

    <div class="kpi-grid room-dynamic-metrics">
      <article class="card kpi-card">
        <div class="section-label">房间总数</div>
        <h3>{{ stats?.total_rooms || 0 }}</h3>
        <p>纳入出租盘点的房间总量</p>
      </article>
      <article class="card kpi-card">
        <div class="section-label">已出租</div>
        <h3>{{ stats?.occupied_rooms || 0 }}</h3>
        <p>当前已出租房间</p>
      </article>
      <article class="card kpi-card">
        <div class="section-label">空置房间</div>
        <h3>{{ stats?.vacancy_rooms || 0 }}</h3>
        <p>当前空置房间</p>
      </article>
      <article class="card kpi-card">
        <div class="section-label">出租率</div>
        <h3>{{ stats?.occupancy_rate || 0 }}%</h3>
        <p>按面积口径统计</p>
      </article>
    </div>

    <article class="card table-card contracts-shell">
      <div class="card-header page-block">
        <div>
          <div class="section-label">房间台账</div>
          <h3 class="card-title">房间台账</h3>
        </div>
      </div>

      <div class="page-block contract-toolbar-shell room-toolbar-shell">
        <div class="filter-cluster room-filter-cluster">
          <select v-model="filters.occupancy" class="select" data-testid="rooms-occupancy-filter" :disabled="roomsLoading" @change="preserveViewportAfterFilterChange">
            <option value="">全部状态</option>
            <option value="空置">空置</option>
            <option value="在租">在租</option>
            <option value="即将到期">即将到期</option>
            <option value="待生效">待生效</option>
            <option value="逾期占用">逾期占用</option>
            <option value="已出租">已出租</option>
          </select>
          <select v-model="filters.tower" class="select" data-testid="rooms-tower-filter" :disabled="roomsLoading" @change="preserveViewportAfterFilterChange">
            <option value="">全部楼栋/区域</option>
            <option v-for="tower in towerOptions" :key="tower" :value="tower">{{ tower }}</option>
          </select>
          <select v-model="filters.use" class="select" data-testid="rooms-use-filter" :disabled="roomsLoading" @change="onUseCategoryChange">
            <option value="">全部用途</option>
            <option value="office">办公</option>
            <option value="retail">商业</option>
          </select>
          <select v-if="filters.use === 'retail'" v-model="filters.subtype" class="select" data-testid="rooms-subtype-filter" :disabled="roomsLoading" @change="preserveViewportAfterFilterChange">
            <option value="">全部业态</option>
            <option v-for="subtype in subtypeOptions" :key="subtype" :value="subtype">{{ subtypeLabel(subtype) }}</option>
          </select>
        </div>

        <div class="action-cluster">
          <button v-if="canEdit" class="btn" @click="openDownload('/api/rooms/export/template')">下载模板</button>
          <button v-if="canEdit" class="btn" @click="triggerImport">导入房间</button>
          <input ref="importInput" class="visually-hidden" type="file" accept=".xlsx,.xls" @change="importRooms" />
          <button v-if="canEdit" class="btn" @click="openDownload('/api/rooms/export/excel')">导出房间</button>
          <button v-if="canEdit" class="btn btn-primary" @click="editingRoom = null; roomFormOpen = true">新增房间</button>
        </div>
      </div>

      <div class="table-wrapper">
        <table class="data-table room-ledger-table" data-testid="rooms-table">
          <thead>
            <tr>
              <th v-if="canEdit" class="check-cell">
                <input
                  type="checkbox"
                  :checked="sortedRooms.length > 0 && selectedRoomIds.length === sortedRooms.length"
                  @change="toggleAllRooms($event.target.checked)"
                />
              </th>
              <th>
                <button class="table-sort-btn centered" data-testid="rooms-sort-room-no" :class="{ active: roomSort.key === 'room_no' }" @click="toggleRoomSort('room_no')">
                  室号 {{ roomSortIndicator('room_no') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn centered" :class="{ active: roomSort.key === 'tower' }" @click="toggleRoomSort('tower')">
                  楼栋/区域 {{ roomSortIndicator('tower') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn centered" :class="{ active: roomSort.key === 'floor' }" @click="toggleRoomSort('floor')">
                  楼层 {{ roomSortIndicator('floor') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn centered" :class="{ active: roomSort.key === 'room_type' }" @click="toggleRoomSort('room_type')">
                  类型 {{ roomSortIndicator('room_type') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn centered" data-testid="rooms-sort-area" :class="{ active: roomSort.key === 'area' }" @click="toggleRoomSort('area')">
                  面积 {{ roomSortIndicator('area') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn centered" :class="{ active: roomSort.key === 'occupancy' }" @click="toggleRoomSort('occupancy')">
                  状态 {{ roomSortIndicator('occupancy') }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn centered" :class="{ active: roomSort.key === 'tenant_name' }" @click="toggleRoomSort('tenant_name')">
                  客户 {{ roomSortIndicator('tenant_name') }}
                </button>
              </th>
              <th class="actions-cell">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="roomsLoading">
              <td :colspan="canEdit ? 9 : 8" class="list-empty">正在加载房间数据</td>
            </tr>
            <tr v-for="room in sortedRooms" v-else :key="room.id">
              <td v-if="canEdit" class="check-cell">
                <input v-model="selectedRoomIds" type="checkbox" :value="room.id" />
              </td>
              <td>{{ room.room_no }}</td>
              <td>{{ room.tower }}</td>
              <td>{{ room.floor }}</td>
              <td>
                <div>{{ room.room_type }}</div>
                <div class="helper-text">{{ getUseCategory(room) === 'office' ? '办公' : '商业' }}</div>
              </td>
              <td>{{ formatArea(room.area) }}</td>
              <td>
                <span class="badge" :class="getRoomStatusTone(getRoomStatus(room))">
                  {{ getRoomStatus(room) }}
                </span>
              </td>
              <td>{{ room.contract?.tenant_name || '--' }}</td>
              <td class="actions-cell">
                <button v-if="canEdit" class="icon-btn" title="编辑房间" @click="editingRoom = room; roomFormOpen = true">改</button>
                <button v-if="canEdit" class="icon-btn danger" title="删除房间" @click="deleteRoom(room)">删</button>
              </td>
            </tr>
            <tr v-if="!roomsLoading && !sortedRooms.length">
              <td :colspan="canEdit ? 9 : 8" class="list-empty">暂无房间数据</td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>

    <RoomFormModal :open="roomFormOpen" :room="editingRoom" @close="roomFormOpen = false" @save="saveRoom" />
  </section>
</template>
