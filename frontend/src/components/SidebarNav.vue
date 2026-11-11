<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  open: Boolean,
  currentTab: {
    type: String,
    required: true
  },
  groups: {
    type: Array,
    required: true
  },
  workspaceLabel: {
    type: String,
    default: ''
  },
  scopeLabel: {
    type: String,
    default: ''
  },
  brand: {
    type: Object,
    default: () => ({})
  }
})

defineEmits(['select', 'preload'])

const expandedGroups = ref({})

function groupContainsCurrent(group) {
  return (group.items || []).some((item) => item.key === props.currentTab)
}

function isExpanded(group) {
  return expandedGroups.value[group.key] !== false
}

function toggleGroup(group) {
  expandedGroups.value = {
    ...expandedGroups.value,
    [group.key]: !isExpanded(group)
  }
}

watch(
  [() => props.currentTab, () => props.groups],
  () => {
    const next = { ...expandedGroups.value }
    for (const group of props.groups) {
      if (next[group.key] == null) {
        next[group.key] = true
      }
      if (groupContainsCurrent(group)) {
        next[group.key] = true
      }
    }
    expandedGroups.value = next
  },
  { immediate: true, deep: true }
)
</script>

<template>
  <aside class="sidebar" :class="{ open }">
    <div class="sidebar-brand">
      <div class="sidebar-brand-logo">
        <img
          v-if="brand.logo_url"
          :src="brand.logo_url"
          :alt="brand.short_name || '租赁管理系统'"
        />
        <span v-else class="sidebar-brand-monogram" data-testid="sidebar-brand-monogram">
          {{ brand.short_name || '楼宇' }}
        </span>
      </div>
      <div class="sidebar-brand-copy">
        <h1>Open Lease Manager</h1>
        <p class="sidebar-workspace-label" data-testid="sidebar-workspace-label">{{ $t(workspaceLabel) }}</p>
        <p v-if="scopeLabel" class="sidebar-scope-label" data-testid="sidebar-scope-label">
          {{ $t(scopeLabel) }}
        </p>
      </div>
    </div>

    <nav class="nav-list">
      <section
        v-for="group in groups"
        :key="group.key"
        class="nav-group"
        :class="{ active: groupContainsCurrent(group) }"
        :data-active="groupContainsCurrent(group) ? 'true' : 'false'"
      >
        <button
          type="button"
          class="nav-group-header"
          :aria-expanded="isExpanded(group)"
          :data-testid="`nav-group-${group.key}`"
          @click="toggleGroup(group)"
        >
          <span class="nav-group-title">{{ $t(group.label) }}</span>
          <span v-if="groupContainsCurrent(group)" class="nav-group-active-dot" aria-hidden="true"></span>
          <span class="nav-group-chevron" aria-hidden="true">{{ $t(isExpanded(group) ? '−' : '+') }}</span>
        </button>
        <div v-if="isExpanded(group)" class="nav-group-items">
          <button
            v-for="item in group.items"
            :key="item.key"
            class="nav-button"
            :class="{ active: currentTab === item.key }"
            :aria-current="currentTab === item.key ? 'page' : undefined"
            :data-testid="`nav-${item.key}`"
            @pointerenter="$emit('preload', item.key)"
            @pointerdown="$emit('preload', item.key)"
            @focus="$emit('preload', item.key)"
            @click="$emit('select', item.key)"
          >
            <span class="nav-button-label">{{ $t(item.label) }}</span>
          </button>
        </div>
      </section>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar-brand-copy {
  min-width: 0;
}

.sidebar-brand-monogram {
  display: grid;
  width: 48px;
  min-height: 40px;
  place-items: center;
  border: 1px solid rgba(96, 165, 250, 0.5);
  border-radius: 8px;
  background: rgba(30, 64, 175, 0.22);
  color: #dbeafe;
  font-size: 10px;
  font-weight: 750;
  line-height: 1.2;
  padding: 4px;
  text-align: center;
}

.sidebar-workspace-label,
.sidebar-scope-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-scope-label {
  margin-top: 5px;
  color: #bfdbfe;
  font-size: 11px;
  font-weight: 650;
}
</style>
