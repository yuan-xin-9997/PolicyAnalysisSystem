import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { setCsrfTokenProvider, setUnauthorizedHandler } from './api/client'
import { createPolicyRouter } from './router'
import { useAuthStore } from './stores/auth'
import './styles/main.css'

const app = createApp(App)
const pinia = createPinia()
const router = createPolicyRouter(pinia)
const auth = useAuthStore(pinia)

setCsrfTokenProvider(() => auth.csrfToken)
setUnauthorizedHandler(async () => {
  auth.clear()
  if (router.currentRoute.value.name !== 'login') {
    await router.replace('/login')
  }
})

app.use(pinia).use(router).mount('#app')
