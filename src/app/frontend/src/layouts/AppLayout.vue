<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { NAVIGATION_ITEMS } from '../navigation'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const visibleItems = computed(() => NAVIGATION_ITEMS.filter((item) => auth.canAccess(item.code)))
const signingOut = ref(false)

async function signOut(): Promise<void> {
  if (signingOut.value) return
  signingOut.value = true
  try {
    await auth.logout()
    await router.replace('/login')
  } finally {
    signingOut.value = false
  }
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">策</span>
        <div>
          <strong>政策分析系统</strong>
          <small>POLICY DESK</small>
        </div>
      </div>

      <nav class="main-nav" aria-label="主导航">
        <RouterLink v-for="item in visibleItems" :key="item.code" :to="item.path">
          <span class="nav-dot" aria-hidden="true"></span>
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="account-panel">
        <div class="account-copy">
          <span class="account-label">当前用户</span>
          <strong>{{ auth.user?.username }}</strong>
          <span class="version">{{ auth.version }}</span>
        </div>
        <button
          class="signout-button"
          type="button"
          aria-label="退出登录"
          :aria-busy="signingOut"
          :disabled="signingOut"
          @click="signOut"
        >
          {{ signingOut ? '退出中…' : '退出' }}
        </button>
      </div>
    </aside>

    <main class="workspace">
      <RouterView />
    </main>
  </div>
</template>
