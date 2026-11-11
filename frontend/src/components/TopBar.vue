<script setup>
import { computed } from 'vue'
import { formatRole } from '../lib/format'

const props = defineProps({
  title: {
    type: String,
    default: ''
  },
  user: {
    type: Object,
    default: null
  },
  theme: {
    type: String,
    default: 'light'
  },
  scopeOptions: {
    type: Array,
    default: () => []
  },
  scopeValue: {
    type: String,
    default: ''
  },
  workspaceOptions: {
    type: Array,
    default: () => []
  },
  workspaceValue: {
    type: String,
    default: ''
  },
  scopeLabel: {
    type: String,
    default: ''
  },
  showSearch: Boolean,
  showPresentation: {
    type: Boolean,
    default: true
  },
  presentationLabel: {
    type: String,
    default: '进入大屏'
  }
})

defineEmits([
  'toggleSidebar',
  'changePassword',
  'changeScope',
  'changeWorkspace',
  'logout',
  'openHelp',
  'openSearch',
  'toggleTheme',
  'presentation'
])

const currentScopeLabel = computed(() => {
  if (props.scopeLabel) return props.scopeLabel
  const current = props.scopeOptions.find((option) => option?.value === props.scopeValue)
  return current?.label || '当前范围'
})
</script>

<template>
  <header class="topbar">
    <div class="topbar-heading">
      <button class="icon-btn mobile-nav-toggle" aria-label="切换导航" @click="$emit('toggleSidebar')">
        <span class="menu-lines" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      </button>
      <div class="topbar-title">
        <h2>{{ title }}</h2>
      </div>
    </div>

    <div class="topbar-user">
      <div class="topbar-context">
        <div
          v-if="workspaceOptions.length > 1"
          class="topbar-workspace-switch"
          role="group"
          aria-label="切换工作区"
        >
          <button
            v-for="option in workspaceOptions"
            :key="option.value"
            type="button"
            class="topbar-workspace-button"
            :class="{ active: workspaceValue === option.value }"
            :aria-pressed="workspaceValue === option.value"
            :data-testid="`workspace-switch-${option.value}`"
            :title="option.description || option.label"
            @click="$emit('changeWorkspace', option.value)"
          >
            {{ option.label }}
          </button>
        </div>

        <div
          v-else-if="workspaceOptions.length === 1"
          class="topbar-current-workspace"
          data-testid="current-workspace"
          :title="workspaceOptions[0].description || workspaceOptions[0].label"
        >
          <span>工作区</span>
          <strong>{{ workspaceOptions[0].label }}</strong>
        </div>

        <div class="topbar-current-scope" data-testid="current-scope">
          <span>当前范围</span>
          <strong>{{ currentScopeLabel }}</strong>
        </div>

        <label v-if="scopeOptions.length > 1" class="topbar-scope">
          <span class="sr-only">切换当前楼宇</span>
          <select
            :value="scopeValue"
            aria-label="切换当前楼宇"
            data-testid="property-scope-selector"
            @change="$emit('changeScope', $event.target.value)"
          >
            <option v-for="option in scopeOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
      </div>

      <div class="topbar-actions">
        <button v-if="showSearch" class="btn" aria-label="查找合同、客户或房号" @click="$emit('openSearch')">查找</button>
        <button
          class="icon-btn topbar-help-button topbar-theme-button"
          type="button"
          :title="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
          :aria-label="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
          data-testid="topbar-theme-toggle"
          @click="$emit('toggleTheme')"
        >
          <svg v-if="theme === 'dark'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
          </svg>
          <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
        </button>
        <button
          v-if="showPresentation"
          class="topbar-presentation-button"
          type="button"
          :title="presentationLabel"
          :aria-label="presentationLabel"
          data-testid="topbar-presentation-toggle"
          @click="$emit('presentation')"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="2" y="3" width="20" height="14" rx="2" />
            <line x1="8" y1="21" x2="16" y2="21" />
            <line x1="12" y1="17" x2="12" y2="21" />
          </svg>
          <span>{{ presentationLabel }}</span>
        </button>
        <button
          class="icon-btn topbar-help-button"
          type="button"
          title="打开帮助"
          aria-label="打开帮助"
          data-testid="topbar-help-toggle"
          @click="$emit('openHelp')"
        >
          ?
        </button>
        <button class="topbar-user-profile" type="button" title="修改密码" @click="$emit('changePassword')">
          <span class="badge info">{{ formatRole(user?.role) }}</span>
          <span class="topbar-user-copy">
            <strong>{{ user?.display_name || user?.username }}</strong>
            <span>{{ user?.username }}</span>
          </span>
        </button>
        <button class="btn" @click="$emit('logout')">退出</button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.topbar-context,
.topbar-actions,
.topbar-workspace-switch {
  min-width: 0;
  display: flex;
  align-items: center;
}

.topbar-user {
  min-width: 0;
  max-width: 100%;
  flex: 0 1 auto;
}

.topbar-context {
  flex: 1 1 auto;
  gap: 8px;
}

.topbar-actions {
  flex: 0 1 auto;
  gap: 8px;
}

.topbar-workspace-switch {
  gap: 3px;
  padding: 3px;
  border: 1px solid rgba(148, 163, 184, 0.28);
  border-radius: 10px;
  background: color-mix(in srgb, var(--surface) 78%, transparent);
}

.topbar-workspace-button {
  min-width: 0;
  height: 28px;
  overflow: hidden;
  border: 0;
  border-radius: 7px;
  padding: 0 9px;
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 12px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-workspace-button:hover,
.topbar-workspace-button:focus-visible {
  background: var(--info-bg);
  color: var(--info-text);
}

.topbar-workspace-button.active {
  background: var(--primary);
  color: #fff;
  box-shadow: 0 5px 12px rgba(37, 99, 235, 0.2);
}

.topbar-current-workspace,
.topbar-current-scope {
  min-width: 0;
  display: grid;
  gap: 1px;
}

.topbar-current-workspace span,
.topbar-current-scope span {
  color: var(--text-faint);
  font-size: 10px;
  line-height: 1;
}

.topbar-current-workspace strong,
.topbar-current-scope strong {
  max-width: 180px;
  overflow: hidden;
  color: var(--text-strong);
  font-size: 12px;
  font-weight: 650;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-scope select {
  min-width: 132px;
  max-width: 220px;
  height: 34px;
  border: 1px solid rgba(148, 163, 184, 0.34);
  border-radius: 8px;
  padding: 0 30px 0 10px;
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 13px;
}

.topbar-scope select:focus-visible {
  outline: 2px solid rgba(59, 130, 246, 0.45);
  outline-offset: 1px;
}

.topbar-help-button {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  padding: 0;
  border-color: rgba(148, 163, 184, 0.34);
  background: transparent;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 680;
  box-shadow: none;
}

.topbar-help-button:hover,
.topbar-help-button:focus-visible {
  border-color: rgba(59, 130, 246, 0.5);
  background: var(--info-bg);
  color: var(--info-text);
}

.topbar-theme-button svg {
  width: 15px;
  height: 15px;
  display: block;
}

.topbar-presentation-button {
  min-width: 0;
  max-width: 220px;
  min-height: 32px;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid rgba(59, 130, 246, 0.34);
  border-radius: 9px;
  padding: 0 10px;
  background: var(--info-bg);
  color: var(--info-text);
  font: inherit;
  font-size: 12px;
  font-weight: 680;
  white-space: nowrap;
}

.topbar-presentation-button span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.topbar-presentation-button:hover,
.topbar-presentation-button:focus-visible {
  border-color: rgba(59, 130, 246, 0.6);
  box-shadow: 0 5px 14px rgba(37, 99, 235, 0.12);
}

.topbar-presentation-button svg {
  width: 15px;
  height: 15px;
  flex: 0 0 15px;
}

@media (max-width: 1180px) {
  .topbar {
    height: auto;
    min-height: 60px;
    flex-wrap: wrap;
    gap: 6px 12px;
    padding-block: 7px;
  }

  .topbar-user {
    min-width: 0;
    flex-wrap: wrap;
  }

  .topbar-current-workspace strong,
  .topbar-current-scope strong {
    max-width: 132px;
  }
}

@media (max-width: 1600px) {
  .topbar {
    height: auto;
    min-height: 60px;
    gap: 6px 12px;
    padding-block: 6px;
  }

  .topbar-user {
    max-width: calc(100% - 160px);
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .topbar-context,
  .topbar-actions {
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .topbar-user-copy {
    display: none;
  }

  .topbar-current-workspace strong,
  .topbar-current-scope strong {
    max-width: 140px;
  }
}

@media (max-width: 640px) {
  .topbar {
    width: 100%;
    max-width: 100%;
    height: auto;
    min-height: 60px;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px 12px;
  }

  .topbar-heading {
    flex: 1 1 120px;
  }

  .topbar-user {
    min-width: 0;
    width: 100%;
    max-width: 100%;
    flex: 1 1 100%;
    flex-wrap: wrap;
    align-items: stretch;
    gap: 6px;
    padding: 4px 6px;
  }

  .topbar-context,
  .topbar-actions {
    min-width: 0;
    max-width: 100%;
    flex: 1 1 100%;
    flex-wrap: wrap;
  }

  .topbar-context {
    align-items: stretch;
  }

  .topbar-actions {
    justify-content: flex-end;
  }

  .topbar-workspace-switch {
    min-width: 0;
    max-width: 100%;
    flex: 1 1 100%;
    flex-wrap: wrap;
  }

  .topbar-workspace-button {
    flex: 1 1 120px;
  }

  .topbar-current-workspace,
  .topbar-current-scope {
    flex: 1 1 120px;
  }

  .topbar-current-workspace strong,
  .topbar-current-scope strong {
    max-width: 100%;
  }

  .topbar-scope {
    min-width: 0;
    flex: 1 1 132px;
  }

  .topbar-scope select {
    width: 100%;
    min-width: 0;
    max-width: none;
  }

  .topbar-help-button {
    width: 32px;
    height: 32px;
    flex-basis: 32px;
  }

  .topbar-presentation-button {
    min-height: 44px;
  }
}
</style>
