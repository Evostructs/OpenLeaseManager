<script setup>
import { formatRole } from '../lib/format'

defineProps({
  title: {
    type: String,
    default: ''
  },
  subtitle: {
    type: String,
    default: ''
  },
  user: {
    type: Object,
    default: null
  }
})

defineEmits(['toggleSidebar', 'changePassword', 'logout'])
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
        <p>{{ subtitle }}</p>
      </div>
    </div>

    <div class="topbar-user">
      <button class="topbar-user-profile" type="button" title="修改密码" @click="$emit('changePassword')">
        <span class="badge info">{{ formatRole(user?.role) }}</span>
        <span class="topbar-user-copy">
          <strong>{{ user?.display_name || user?.username }}</strong>
          <span>{{ user?.username }}</span>
        </span>
      </button>
      <button class="btn" @click="$emit('logout')">退出</button>
    </div>
  </header>
</template>
