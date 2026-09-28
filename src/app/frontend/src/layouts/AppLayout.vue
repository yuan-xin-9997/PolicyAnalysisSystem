<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { NAVIGATION_ITEMS } from '../navigation'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const visibleItems = computed(() => NAVIGATION_ITEMS.filter((item) => auth.canAccess(item.code)))
const signingOut = ref(false)
const mobileNavigationOpen = ref(false)

function closeMobileNavigation(): void {
  mobileNavigationOpen.value = false
}

function toggleMobileNavigation(): void {
  mobileNavigationOpen.value = !mobileNavigationOpen.value
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeMobileNavigation()
}

watch(
  () => route.fullPath,
  () => closeMobileNavigation(),
)

watch(mobileNavigationOpen, (open) => {
  document.body.classList.toggle('navigation-open', open)
})

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.body.classList.remove('navigation-open')
})

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
    <header class="mobile-header">
      <div class="brand compact-brand">
        <span class="brand-mark" aria-hidden="true">策</span>
        <div>
          <strong>政策分析系统</strong>
          <small>POLICY DESK</small>
        </div>
      </div>
      <button
        class="menu-toggle"
        type="button"
        aria-controls="primary-sidebar"
        :aria-expanded="mobileNavigationOpen"
        :aria-label="mobileNavigationOpen ? '关闭导航菜单' : '打开导航菜单'"
        @click="toggleMobileNavigation"
      >
        <span aria-hidden="true"></span>
        <span aria-hidden="true"></span>
        <span aria-hidden="true"></span>
      </button>
    </header>

    <button
      v-if="mobileNavigationOpen"
      class="navigation-backdrop"
      type="button"
      aria-label="关闭导航遮罩"
      data-testid="navigation-backdrop"
      @click="closeMobileNavigation"
    ></button>

    <aside id="primary-sidebar" class="sidebar" :class="{ 'is-open': mobileNavigationOpen }">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">策</span>
        <div>
          <strong>政策分析系统</strong>
          <small>POLICY DESK</small>
        </div>
      </div>

      <nav class="main-nav" aria-label="主导航">
        <RouterLink
          v-for="item in visibleItems"
          :key="item.code"
          :to="item.path"
          @click="closeMobileNavigation"
        >
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
