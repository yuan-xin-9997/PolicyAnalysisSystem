// Real Chromium acceptance against a running deployment; never writes secrets, traces or HAR files.
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { createRequire } from 'node:module'
import { mkdir, readdir, readFile } from 'node:fs/promises'
import path from 'node:path'

const require = createRequire(new URL('../src/app/frontend/package.json', import.meta.url))
const { chromium, request } = require('@playwright/test')
const base = process.env.AI_WEB_BASE_URL?.replace(/\/$/, '')
assert.ok(base && process.env.AI_WEB_USERNAME && process.env.AI_WEB_PASSWORD, 'Set AI_WEB connection environment')
const browser = await chromium.launch({ headless: true })
const anonymous = await request.newContext()
const cleanup = []
let stage = 'login'
let adminPage, adminCsrf, testUsername

async function login(page, username, password) {
  await page.context().clearCookies()
  await page.goto(base + '/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码', { exact: true }).fill(password)
  const loginResponse = page.waitForResponse(r => r.url().endsWith('/api/v1/auth/login') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  const response = await loginResponse
  assert.ok(response.ok(), 'Browser login must succeed')
  return (await response.json()).csrf_token
}

async function verifyFlow(page, context, username, password, ordinary = false) {
  let csrf = await login(page, username, password)
  if (!ordinary) { adminPage = page; adminCsrf = csrf }
  stage = ordinary ? 'ordinary user management' : 'administrator management'
  await page.goto(base + '/settings/ai-access')
  await page.getByRole('heading', { name: 'AI 接入', exact: true }).waitFor()
  await page.getByRole('button', { name: '刷新权限与列表' }).waitFor()
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b => b.textContent === '刷新权限与列表')?.disabled)
  assert.ok(await page.getByLabel('查看政策', { exact: true }).isChecked(), 'Default must be policy read only')
  if (ordinary) {
    assert.ok(await page.getByLabel('维护采集规则（管理员）').count() === 0, 'No admin scope in ordinary UI')
    const forged = await page.request.post(base + '/api/v1/credentials', { headers: { 'X-CSRF-Token': csrf },
      data: { name: 'forbidden', scopes: ['rules:write'], expires_at: new Date(Date.now() + 86400000).toISOString() } })
    assert.ok(forged.status() === 403, 'Forged scope must be refused')
  }
  const label = '浏览器验收-' + randomUUID().slice(0, 8)
  await page.getByLabel('凭据名称').fill(label)
  const createdResponse = page.waitForResponse(r => r.url().endsWith('/api/v1/credentials') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '创建凭据', exact: true }).click()
  const response = await createdResponse
  assert.ok(response.status() === 201 && response.headers()['cache-control']?.includes('no-store'), 'Creation must not be cached')
  const issued = await response.json()
  const id = issued.id
  delete issued.secret
  cleanup.push({ page, id, csrf })
  const input = page.getByLabel('完整密钥，仅显示一次')
  let secret = await input.inputValue()
  assert.ok(secret.startsWith('pa_'), 'Must display a real independent credential')
  assert.ok(await page.getByRole('button', { name: '关闭并清除密钥' }).isDisabled(), 'Require explicit save acknowledgement')
  await page.getByRole('button', { name: '复制密钥' }).click()
  await page.getByText('已复制，请粘贴到受保护的凭据存储。', { exact: true }).waitFor()
  assert.ok(await page.evaluate(() => navigator.clipboard.readText()) === secret, 'Clipboard copy must work')
  await page.evaluate(() => navigator.clipboard.writeText(''))
  await page.getByLabel('我已安全保存密钥').check()
  await page.getByRole('button', { name: '关闭并清除密钥' }).click()
  await input.waitFor({ state: 'detached' })
  const headers = { Authorization: 'Bearer ' + secret }
  const query = await anonymous.get(base + '/api/v1/policies?page_size=1', { headers })
  assert.ok(query.ok() && Array.isArray((await query.json()).items), 'Browser-issued key must query real API')
  const info = await (await page.request.get(base + '/api/v1/ai-access')).json()
  const exchanged = await anonymous.post(info.token_url, { form: {
    grant_type: 'client_credentials', client_id: id, client_secret: secret, resource: info.mcp_url,
  } })
  assert.ok(exchanged.ok(), 'OAuth exchange must succeed')
  let token = (await exchanged.json()).access_token
  const mcpHeaders = { Authorization: 'Bearer ' + token, Accept: 'application/json, text/event-stream' }
  const init = await anonymous.post(info.mcp_url, { headers: mcpHeaders, data: {
    jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-11-25', capabilities: {},
      clientInfo: { name: 'browser-acceptance', version: '1' } },
  } })
  assert.ok(init.ok() && (await init.json()).result?.capabilities?.tools, 'Real MCP initialization')
  const tools = await anonymous.post(info.mcp_url, { headers: mcpHeaders,
    data: { jsonrpc: '2.0', id: 2, method: 'tools/list' } })
  assert.ok((await tools.json()).result?.tools?.length === 21, 'Real MCP discovery')
  if (ordinary) {
    const other = cleanup.find(entry => entry.id !== id)
    assert.ok((await page.request.delete(base + '/api/v1/credentials/' + other.id,
      { headers: { 'X-CSRF-Token': csrf } })).status() === 404, 'Cross-user revoke refused')
    const ownList = await (await page.request.get(base + '/api/v1/ai-access/credentials')).json()
    assert.ok(!ownList.items.some(entry => entry.id === other.id), 'Only own credentials visible')
    const adminHeaders = { 'X-CSRF-Token': adminCsrf }
    assert.ok((await adminPage.request.patch(base + `/api/v1/users/${username}/pages`,
      { headers: adminHeaders, data: { pages: [] } })).ok(), 'Reduce test user permissions')
    assert.ok((await anonymous.get(base + '/api/v1/policies', { headers })).status() === 403, 'Reduced permission affects old key')
    await adminPage.request.patch(base + `/api/v1/users/${username}/pages`, { headers: adminHeaders, data: { pages: ['policies'] } })
    await adminPage.request.patch(base + `/api/v1/users/${username}/status`, { headers: adminHeaders, data: { is_active: false } })
    assert.ok((await anonymous.get(base + '/api/v1/policies', { headers })).status() === 401, 'Disabled user cannot use old key')
    await adminPage.request.patch(base + `/api/v1/users/${username}/status`, { headers: adminHeaders, data: { is_active: true } })
    csrf = await login(page, username, password)
    cleanup[cleanup.length - 1].csrf = csrf
  }
  await page.goto(base + '/settings/ai-access')
  const metadata = await (await page.request.get(base + '/api/v1/ai-access/credentials')).text()
  assert.ok(!metadata.includes(secret) && !metadata.includes('"secret"'), 'Lists must not return plaintext')
  assert.ok(await page.getByLabel('完整密钥，仅显示一次').count() === 0, 'Reopening must not redisplay key')
  const row = page.getByRole('row').filter({ hasText: label })
  await row.getByRole('button', { name: '撤销', exact: true }).click()
  await page.getByText(/关联的短期令牌也会立即失效/).waitFor()
  await page.getByRole('button', { name: '确认撤销', exact: true }).click()
  await row.getByText('已撤销', { exact: true }).waitFor()
  assert.ok((await anonymous.get(base + '/api/v1/policies', { headers })).status() === 401, 'Revoked key refused')
  assert.ok((await anonymous.post(info.mcp_url, { headers: mcpHeaders,
    data: { jsonrpc: '2.0', id: 3, method: 'tools/list' } })).status() === 401, 'Associated OAuth token refused')
  const state = await page.evaluate(() => ({ storage: JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]),
    url: location.href, html: document.documentElement.outerHTML }))
  assert.ok(!JSON.stringify(state).includes(secret) && !JSON.stringify(state).includes(token), 'No browser storage/URL/DOM leaks')
  if (process.env.AI_WEB_SCREENSHOT_DIR) {
    await mkdir(process.env.AI_WEB_SCREENSHOT_DIR, { recursive: true })
    await page.screenshot({ path: path.join(process.env.AI_WEB_SCREENSHOT_DIR, ordinary ? 'ordinary-desktop.png' : 'admin-desktop.png'), fullPage: true })
  }
  await page.setViewportSize({ width: 320, height: 844 })
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.getByRole('button', { name: '打开导航菜单' }).click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: 'AI 接入', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.sidebar')?.getBoundingClientRect().right <= 0)
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '320px mobile layout must not overflow')
  if (process.env.AI_WEB_SCREENSHOT_DIR) {
    await mkdir(process.env.AI_WEB_SCREENSHOT_DIR, { recursive: true })
    await page.screenshot({ path: path.join(process.env.AI_WEB_SCREENSHOT_DIR, ordinary ? 'ordinary-mobile.png' : 'admin-mobile.png'), fullPage: true, animations: 'disabled' })
  }
  const dir = process.env.AI_WEB_DIST_DIR
  if (dir) for (const file of await readdir(path.join(dir, 'assets'))) {
    if (!/\.(js|css)$/.test(file)) continue
    const content = await readFile(path.join(dir, 'assets', file), 'utf8')
    assert.ok(!content.includes(secret) && !content.includes(token), 'No build artifact leaks')
  }
  secret = ''; token = ''
  return { user: ordinary ? 'ordinary' : 'admin', created: true, copied: true, api: true, mcp_tools: 21,
    revoked: true, associated_token_revoked: true, reopened_without_secret: true, mobile_width: 320 }
}

try {
  const context = await browser.newContext({ locale: 'zh-CN', timezoneId: 'Asia/Shanghai', permissions: ['clipboard-read', 'clipboard-write'] })
  const page = await context.newPage()
  const consoleLines = []
  page.on('console', message => consoleLines.push(message.text()))
  const results = [await verifyFlow(page, context, process.env.AI_WEB_USERNAME, process.env.AI_WEB_PASSWORD)]
  if (process.env.AI_WEB_VERIFY_USER === '1') {
    testUsername = 'aiverify' + Date.now().toString(36)
    const password = randomUUID() + 'Aa9!'
    const created = await adminPage.request.post(base + '/api/v1/users', { headers: { 'X-CSRF-Token': adminCsrf },
      data: { username: testUsername, password, role: 'user', pages: ['policies'] } })
    assert.ok(created.status() === 201, 'Create isolated temporary ordinary test user')
    const ordinary = await browser.newContext({ locale: 'zh-CN', timezoneId: 'Asia/Shanghai', permissions: ['clipboard-read', 'clipboard-write'] })
    results.push(await verifyFlow(await ordinary.newPage(), ordinary, testUsername, password, true))
  }
  assert.ok(!consoleLines.some(line => /pa_[A-Za-z0-9_-]{30,}/.test(line)), 'No frontend credential logs')
  console.log(JSON.stringify({ browser: 'Chromium', base, results, secrets_logged: false }))
} catch (error) {
  console.error(JSON.stringify({ stage, error: error.name, message: 'Browser acceptance failed; no secrets included.' }))
  process.exitCode = 1
} finally {
  for (const entry of cleanup) {
    try { await entry.page.request.delete(base + '/api/v1/credentials/' + entry.id, { headers: { 'X-CSRF-Token': entry.csrf } }) } catch { /* no secrets in diagnostics */ }
  }
  if (testUsername && adminPage) {
    try { await adminPage.request.patch(base + `/api/v1/users/${testUsername}/status`,
      { headers: { 'X-CSRF-Token': adminCsrf }, data: { is_active: false } }) } catch { /* administrator can inspect failed cleanup */ }
  }
  await anonymous.dispose()
  await browser.close()
}
