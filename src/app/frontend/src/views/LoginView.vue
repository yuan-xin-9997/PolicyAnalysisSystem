<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMessage = ref('')

async function submit(): Promise<void> {
  if (loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    await auth.login(username.value, password.value)
    password.value = ''
    await router.replace(auth.firstAccessiblePath())
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '登录失败，请稍后重试。'
    password.value = ''
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-intro" aria-labelledby="product-title">
      <span class="eyebrow">POLICY INTELLIGENCE</span>
      <h1 id="product-title">政策信息，<br />清晰抵达。</h1>
      <p>集中管理政策资料、采集任务与系统权限，为研判工作建立可靠入口。</p>
    </section>

    <section class="login-card" aria-labelledby="login-title">
      <div class="login-card-heading">
        <span class="brand-mark" aria-hidden="true">策</span>
        <div>
          <h2 id="login-title">欢迎登录</h2>
          <p>使用系统账户继续</p>
        </div>
      </div>

      <form @submit.prevent="submit">
        <div class="field">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            name="username"
            type="text"
            autocomplete="username"
            required
            :disabled="loading"
          />
        </div>
        <div class="field">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="password"
            name="password"
            type="password"
            autocomplete="current-password"
            required
            :disabled="loading"
          />
        </div>
        <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>
        <button class="primary-button" type="submit" :disabled="loading" :aria-busy="loading">
          {{ loading ? '正在登录…' : '登录' }}
        </button>
      </form>
    </section>
  </main>
</template>
