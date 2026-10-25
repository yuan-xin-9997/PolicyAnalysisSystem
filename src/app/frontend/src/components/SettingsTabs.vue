<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
const auth = useAuthStore()
</script>

<template>
  <nav class="settings-tabs" aria-label="设置导航">
    <RouterLink v-if="auth.canAccess('settings')" to="/settings" exact-active-class="active">系统配置</RouterLink>
    <RouterLink to="/settings/ai-access" exact-active-class="active">AI 接入</RouterLink>
  </nav>
</template>
