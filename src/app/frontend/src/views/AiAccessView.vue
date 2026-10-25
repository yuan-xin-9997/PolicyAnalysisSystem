<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

import { ApiError, apiRequest } from '../api/client'
import { clientExample, type AccessInfo, type Credential, type CredentialPage, type IssuedCredential } from '../api/ai-access'
import SettingsTabs from '../components/SettingsTabs.vue'
import { useAuthStore } from '../stores/auth'
import { formatBeijingTime } from '../utils/time'

const info = ref<AccessInfo | null>(null)
const auth = useAuthStore()
let authGeneration = 0
const entries = ref<Credential[]>([])
const page = ref(1)
const total = ref(0)
const pageSize = 10
const loading = ref(true)
const saving = ref(false)
const revoking = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const name = ref('')
const days = ref(7)
const scopes = ref<string[]>([])
const secret = ref<IssuedCredential | null>(null)
const saved = ref(false)
const copyMessage = ref('')
const revokeTarget = ref<Credential | null>(null)
const secretDialog = ref<HTMLDialogElement | null>(null)
const secretInput = ref<HTMLTextAreaElement | null>(null)
const revokeDialog = ref<HTMLDialogElement | null>(null)
let disposed = false
const now = ref(Date.now())
let clock: ReturnType<typeof setInterval> | undefined
const canCreate = computed(() => !loading.value && !saving.value && !!name.value.trim() && scopes.value.length > 0)
const pythonExample = computed(() => info.value ? clientExample(info.value) : '')
const apiExample = computed(() => info.value ? `# POLICY_API_BASE=${info.value.issuer}
# 将 POLICY_API_KEY 设为受保护存储中的凭据；以下查询无副作用。
curl "$POLICY_API_BASE/api/v1/policies?page=1&page_size=10" \\
  -H "Authorization: Bearer $POLICY_API_KEY"` : '')

onMounted(() => {
  void load()
  window.addEventListener('beforeunload', beforeUnload)
  window.addEventListener('pagehide', onPageHide)
  clock = setInterval(() => { now.value = Date.now() }, 1000)
})
onBeforeUnmount(() => {
  disposed = true
  clearSecret()
  clearInterval(clock)
  window.removeEventListener('beforeunload', beforeUnload)
  window.removeEventListener('pagehide', onPageHide)
})
watch(() => auth.user?.id, () => { authGeneration += 1; clearSecret() }, { flush: 'sync' })
onBeforeRouteLeave(() => {
  if (!auth.user) { clearSecret(); return true }
  if (saving.value) return false
  if (secret.value && !window.confirm('完整密钥仅显示这一次。离开后无法再次查看，确认已保存并离开？')) return false
  clearSecret()
  return true
})

function beforeUnload(event: BeforeUnloadEvent): void {
  if (secret.value || saving.value) { event.preventDefault(); event.returnValue = '' }
}
function show(dialog: HTMLDialogElement | null): void {
  if (dialog?.showModal) dialog.showModal()
  else dialog?.setAttribute('open', '')
}
function hide(dialog: HTMLDialogElement | null): void {
  if (dialog?.close) dialog.close()
  else dialog?.removeAttribute('open')
}
function clearSecret(): void {
  secret.value = null
  if (secretInput.value) secretInput.value.value = ''
  copyMessage.value = ''
  hide(secretDialog.value)
}
function onPageHide(): void { authGeneration += 1; clearSecret() }
async function loadInfo(initial = false): Promise<void> {
  const result = await apiRequest<AccessInfo>('/ai-access', { cache: 'no-store' })
  if (disposed) return
  info.value = result
  const allowed = new Set(result.scopes.map(scope => scope.code))
  const defaultScope = result.scopes.find(scope => scope.code === 'policies:read') || result.scopes.find(scope => scope.read_only)
  scopes.value = initial
    ? (defaultScope ? [defaultScope.code] : [])
    : scopes.value.filter(scope => allowed.has(scope))
}
async function loadList(targetPage = page.value): Promise<void> {
  const result = await apiRequest<CredentialPage>('/ai-access/credentials', {
    cache: 'no-store', query: { page: targetPage, page_size: pageSize },
  })
  if (disposed) return
  entries.value = result.items
  total.value = result.total
  page.value = result.page
}
async function load(targetPage = page.value): Promise<void> {
  if (secret.value || saving.value) return
  loading.value = true
  errorMessage.value = ''
  try { await loadInfo(!info.value); await loadList(targetPage) }
  catch (error) { errorMessage.value = error instanceof ApiError ? error.message : 'AI 接入加载失败，请重试。' }
  finally { loading.value = false }
}
async function changePage(delta: number): Promise<void> { await load(page.value + delta) }
async function create(): Promise<void> {
  if (!canCreate.value) return
  saving.value = true
  const generation = authGeneration
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await apiRequest<IssuedCredential>('/credentials', { method: 'POST', cache: 'no-store',
      body: JSON.stringify({ name: name.value.trim(), scopes: scopes.value,
        expires_at: new Date(Date.now() + days.value * 86400000).toISOString() }) })
    if (disposed || generation !== authGeneration || !auth.user) return
    secret.value = result
    saved.value = false
    copyMessage.value = ''
    name.value = ''
    await nextTick()
    show(secretDialog.value)
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '创建失败，请刷新列表核查是否已创建，必要时撤销旧凭据后重建。'
    if (error instanceof ApiError && error.status === 403) {
      try { await loadInfo() } catch { /* Keep the original safe error visible. */ }
    }
  } finally { saving.value = false }
}
async function copy(): Promise<void> {
  if (!secret.value) return
  try { await navigator.clipboard.writeText(secret.value.secret); copyMessage.value = '已复制，请粘贴到受保护的凭据存储。' }
  catch { copyMessage.value = '复制失败，请手动选中并保存密钥。' }
}
async function closeSecret(): Promise<void> {
  if (!saved.value) return
  clearSecret()
  page.value = 1
  await load()
}
async function requestRevoke(entry: Credential): Promise<void> {
  revokeTarget.value = entry
  await nextTick()
  show(revokeDialog.value)
}
function cancelRevoke(): void {
  if (revoking.value) return
  revokeTarget.value = null
  hide(revokeDialog.value)
}
async function revoke(): Promise<void> {
  if (!revokeTarget.value || revoking.value) return
  revoking.value = true
  errorMessage.value = ''
  try {
    const id = revokeTarget.value.id
    await apiRequest<void>(`/credentials/${encodeURIComponent(id)}`, { method: 'DELETE' })
    const entry = entries.value.find(item => item.id === id)
    if (entry) { entry.status = 'revoked'; entry.revoked_at = new Date().toISOString() }
    revokeTarget.value = null
    hide(revokeDialog.value)
    successMessage.value = '凭据及关联短期令牌已撤销。'
    try { await loadList() }
    catch { errorMessage.value = '撤销已成功，但列表刷新失败，请稍后刷新。' }
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '撤销失败，请重试；请勿认为凭据已失效。'
  } finally { revoking.value = false }
}
function status(entry: Credential): string {
  return entry.revoked_at || entry.status === 'revoked' ? '已撤销'
    : Date.parse(entry.expires_at) <= now.value || entry.status === 'expired' ? '已过期' : '有效'
}
function scopeLabel(code: string): string { return info.value?.scopes.find(scope => scope.code === code)?.label || code }
</script>

<template>
  <section class="page-panel ai-access" aria-labelledby="ai-access-title">
    <SettingsTabs />
    <header class="page-heading">
      <div><span class="eyebrow">AI &amp; AUTOMATION</span><h1 id="ai-access-title">AI 接入</h1>
        <p>管理自己的 API 与 MCP 凭据。实际权限始终受你当前的角色和页面权限约束。</p></div>
      <button type="button" :disabled="loading || saving || !!secret" @click="load()">刷新权限与列表</button>
    </header>
    <p v-if="errorMessage" role="alert" class="state-card error-state">{{ errorMessage }}</p>
    <p v-if="successMessage" role="status">{{ successMessage }}</p>
    <p v-if="loading" role="status">正在加载 AI 接入…</p>
    <button v-if="errorMessage && !info" type="button" @click="load()">重试加载</button>
    <template v-if="info">
      <form aria-label="创建 AI 凭据" class="ai-card" @submit.prevent="create">
        <h2>创建凭据</h2>
        <div class="ai-form-fields">
          <label>凭据名称<input v-model="name" maxlength="128" required autocomplete="off" placeholder="例如：政策研究助手" /></label>
          <label>有效期<select v-model.number="days"><option v-for="value in [1, 7, 30, 90, 365]" :key="value" :value="value">{{ value }} 天</option></select></label>
        </div>
        <fieldset><legend>权限范围（默认最小只读权限）</legend>
          <p v-if="!info.scopes.length">当前没有可授予的业务权限，请联系管理员调整页面权限。</p>
          <label v-for="scope in info.scopes" :key="scope.code" class="ai-scope">
            <input v-model="scopes" type="checkbox" :value="scope.code" :aria-label="scope.label" />
            <span>{{ scope.label }}<small>{{ scope.description }}</small></span>
          </label>
        </fieldset>
        <p class="ai-help">只为自己签发；管理员能力仅管理员可选。创建后完整密钥只显示一次。</p>
        <button class="primary-button" type="submit" :disabled="!canCreate">{{ saving ? '正在创建…' : '创建凭据' }}</button>
      </form>

      <section class="ai-card" aria-labelledby="credential-list-title">
        <h2 id="credential-list-title">我的凭据</h2><p>仅展示元数据，不返回完整密钥。时间为北京时间。</p>
        <p v-if="!loading && !errorMessage && !entries.length">暂无凭据</p>
        <div v-if="entries.length" class="table-wrap"><table>
          <thead><tr><th>名称 / 标识</th><th>权限范围</th><th>创建时间</th><th>过期时间</th><th>状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="entry in entries" :key="entry.id">
            <td>{{ entry.name }}<small class="ai-id">{{ entry.id }}</small></td>
            <td><span v-for="scope in entry.scopes" :key="scope" class="ai-scope-label">{{ scopeLabel(scope) }}{{ entry.effective_scopes.includes(scope) ? '' : '（当前权限不可用）' }}</span></td>
            <td>{{ formatBeijingTime(entry.created_at) }}</td><td>{{ formatBeijingTime(entry.expires_at) }}</td>
            <td>{{ status(entry) }}</td><td><button type="button" :disabled="!!entry.revoked_at || entry.status === 'revoked'" @click="requestRevoke(entry)">撤销</button></td>
          </tr></tbody>
        </table></div>
        <div class="pagination"><button type="button" :disabled="page <= 1 || loading" @click="changePage(-1)">上一页</button>
          <span>第 {{ page }} 页 / 共 {{ total }} 条</span><button type="button" :disabled="page * pageSize >= total || loading" @click="changePage(1)">下一页</button></div>
      </section>

      <section class="ai-card" aria-labelledby="ai-address-title"><h2 id="ai-address-title">当前环境与接入示例</h2>
        <dl class="ai-addresses"><dt>API</dt><dd><code>{{ info.api_url }}</code></dd>
          <dt>OpenAPI</dt><dd><a :href="info.openapi_url" target="_blank" rel="noopener">{{ info.openapi_url }}</a></dd>
          <dt>交互文档</dt><dd><a :href="info.docs_url" target="_blank" rel="noopener">{{ info.docs_url }}</a></dd>
          <dt>MCP</dt><dd><code>{{ info.mcp_url }}</code></dd></dl>
        <p>API 使用独立 Bearer 凭据。MCP 使用 OAuth client_credentials 扩展：创建后的标识作为 client_id，密钥作为 client_secret，由客户端自动换取和续取最长 15 分钟的令牌。</p>
        <p>不要直接将 API Key 当作通用 MCP 令牌。下面仅使用环境变量，不包含本次创建的密钥或令牌；示例查询无副作用。</p>
        <details><summary>API 查询示例</summary><pre>{{ apiExample }}</pre></details>
        <details><summary>官方 Python MCP 客户端示例（自动鉴权）</summary><pre>{{ pythonExample }}</pre></details>
        <h3>客户端兼容性</h3><ul><li v-for="client in info.clients" :key="client.name"><strong>{{ client.name }}：{{ client.status }}</strong> — {{ client.description }}</li></ul>
        <details><summary>本系统 MCP 工具清单（{{ info.tools.length }} 个）</summary>
          <p>可发现工具不代表有调用权限；仍校验凭据范围与本人当前权限。写入需要幂等键，MCP 写工具还需明确确认。</p>
          <ul><li v-for="tool in info.tools" :key="tool.name"><code>{{ tool.name }}</code>（{{ tool.annotations.readOnlyHint ? '只读' : tool.annotations.destructiveHint ? '修改，需确认' : '写入，需确认' }}）：{{ tool.description }}</li></ul>
        </details>
      </section>
    </template>

    <dialog ref="secretDialog" class="ai-dialog" aria-labelledby="secret-title" @cancel.prevent>
      <template v-if="secret"><h2 id="secret-title">完整密钥仅显示一次</h2>
        <p>关闭后无法再次查看。请安全保存；遗失时应撤销旧凭据并重新创建。密钥只暂存在当前页面内存。</p>
        <p>凭据标识：<code>{{ secret.id }}</code></p>
        <label>完整密钥，仅显示一次<textarea ref="secretInput" :value="secret.secret" readonly autocomplete="off" spellcheck="false" /></label>
        <button type="button" @click="copy">复制密钥</button><p v-if="copyMessage" role="status">{{ copyMessage }}</p>
        <label class="ai-scope"><input v-model="saved" type="checkbox" />我已安全保存密钥</label>
        <button type="button" :disabled="!saved" @click="closeSecret">关闭并清除密钥</button>
      </template>
    </dialog>
    <dialog ref="revokeDialog" class="ai-dialog" aria-labelledby="revoke-title" @cancel.prevent="cancelRevoke">
      <template v-if="revokeTarget"><h2 id="revoke-title">确认撤销凭据</h2>
        <p>撤销「{{ revokeTarget.name }}」后，该凭据及关联的短期令牌也会立即失效，使用它的 AI 或自动化程序将无法继续调用。此操作不能恢复。</p>
        <p v-if="errorMessage" role="alert">{{ errorMessage }}</p>
        <div class="ai-actions"><button type="button" :disabled="revoking" @click="cancelRevoke">取消</button>
          <button type="button" :disabled="revoking" @click="revoke">{{ revoking ? '撤销中…' : '确认撤销' }}</button></div>
      </template>
    </dialog>
  </section>
</template>
