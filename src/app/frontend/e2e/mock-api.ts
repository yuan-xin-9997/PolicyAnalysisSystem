import type { Page, Route } from '@playwright/test'

type HttpMethod = 'GET' | 'POST' | 'PATCH'

const jsonHeaders = { 'Content-Type': 'application/json' }

export async function installMockApi(page: Page): Promise<void> {
  let loggedIn = false
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method() as HttpMethod

    if (path === '/api/v1/auth/me') {
      return fulfillJson(route, loggedIn ? user() : error('INVALID_SESSION', '登录状态已失效。'), loggedIn ? 200 : 401)
    }
    if (path === '/api/v1/auth/login' && method === 'POST') {
      loggedIn = true
      return fulfillJson(route, { user: user(), csrf_token: 'csrf-e2e-token' })
    }
    if (path === '/api/v1/auth/logout' && method === 'POST') {
      loggedIn = false
      return route.fulfill({ status: 204 })
    }
    if (path === '/api/v1/system/info') return fulfillJson(route, { version: 'v0.e2e', commit_sha: 'e2etest' })
    if (path === '/api/v1/policies/filters') return fulfillJson(route, policyFilterOptions())
    if (path === '/api/v1/policies') return fulfillJson(route, policyPage())
    if (path === '/api/v1/policies/7') return fulfillJson(route, policyDetail())
    if (path === '/api/v1/tasks' && method === 'GET') return fulfillJson(route, taskPage())
    if (path === '/api/v1/tasks' && method === 'POST') return fulfillJson(route, task(42), 201)
    if (path === '/api/v1/tasks/42') return fulfillJson(route, task(42))
    if (path === '/api/v1/tasks/42/items') return fulfillJson(route, taskItems())
    if (path === '/api/v1/tasks/42/logs') return fulfillJson(route, taskLogs())
    if (path === '/api/v1/collection-rules') return fulfillJson(route, [rule()])
    if (path === '/api/v1/sources') return fulfillJson(route, [source()])
    if (path === '/api/v1/policy-categories') return fulfillJson(route, [category()])
    if (path === '/api/v1/analysis/tasks' && method === 'POST') return fulfillJson(route, { task_id: 51, status: 'pending' })
    if (path === '/api/v1/analysis/tasks' && method === 'GET') return fulfillJson(route, analysisTaskPage())
    if (path === '/api/v1/analysis/tasks/51') return fulfillJson(route, analysisTask(51))
    if (path === '/api/v1/analysis/tasks/51/words') return fulfillJson(route, analysisWords())
    if (path === '/api/v1/analysis/tasks/51/relations') return fulfillJson(route, analysisRelations())
    if (path === '/api/v1/analysis/tasks/51/logs') return fulfillJson(route, analysisLogs())
    return fulfillJson(route, error('NOT_FOUND', `未模拟接口：${path}`), 404)
  })
}

async function fulfillJson(route: Route, body: object, status = 200): Promise<void> {
  await route.fulfill({ status, headers: jsonHeaders, body: JSON.stringify(body) })
}

function error(code: string, message: string): object {
  return { error: { code, message, request_id: 'e2e-request', details: {} } }
}

function user(): object {
  return {
    id: 1,
    username: 'admin',
    role: 'admin',
    page_permissions: ['policies', 'tasks', 'push', 'analysis', 'users', 'settings'],
  }
}

function source(): object {
  return {
    id: 1,
    code: 'xinhua',
    name: '新华网',
    organization: '新华社',
    base_url: 'https://www.news.cn',
    adapter_type: 'xinhua',
    allowed_domains: ['news.cn'],
    is_active: true,
  }
}

function category(): object {
  return { id: 1, code: 'politics', name: '政治', description: null, is_active: true }
}

function rule(): object {
  return {
    id: 9,
    name: '中央政策',
    source: source(),
    category: category(),
    include_keywords: ['政治局'],
    exclude_keywords: [],
    history_years: 5,
    discovery: { rss_urls: ['https://www.news.cn/rss.xml'], channel_urls: [] },
    is_active: true,
    trigger_mode: 'manual',
    cron_expression: null,
    schedule_timezone: 'Asia/Shanghai',
    schedule_enabled: false,
    next_run_at: null,
    last_run_at: null,
    created_at: '2026-07-31T04:00:00Z',
    updated_at: '2026-07-31T04:00:00Z',
  }
}

function policyPage(): object {
  return {
    items: [
      {
        id: 7,
        title: '中央政治局召开会议',
        canonical_url: 'https://www.news.cn/politics/7',
        publisher: '新华社',
        category: category(),
        source: source(),
        published_at: '2026-07-31T01:00:00Z',
        first_crawled_at: '2026-07-31T04:00:00Z',
        last_crawled_at: '2026-07-31T04:00:00Z',
        content_hash: 'hash7',
        latest_task_id: 42,
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
    sort_by: 'published_at',
    sort_order: 'desc',
  }
}

function policyFilterOptions(): object {
  return {
    publishers: ['新华社'],
    categories: [{ id: 1, code: 'politics', name: '政治' }],
    sources: [{ id: 1, code: 'xinhua', name: '新华网' }],
  }
}

function policyDetail(): object {
  return {
    ...(policyPage() as { items: object[] }).items[0],
    content_text:
      '第一段政策正文。\n\n第二段政策正文，用于验证分段与首行缩进。\n<script>window.e2eLeak=true</script>',
  }
}

function task(id: number): object {
  return {
    id,
    rule_id: 9,
    trigger_type: 'manual',
    status: 'succeeded',
    requested_by: 1,
    scheduled_for: null,
    started_at: '2026-07-31T04:00:00Z',
    finished_at: '2026-07-31T04:01:00Z',
    cancel_requested_at: null,
    error_summary: null,
    progress: { processed: 2, discovered: 2 },
    counts: { success: 1, duplicate: 1, filtered: 0, failed: 0, total_terminal_items: 2 },
  }
}

function taskPage(): object {
  return { items: [task(42)], total: 1, page: 1, page_size: 20 }
}

function taskItems(): object {
  return {
    items: [
      {
        id: 1,
        candidate_url: 'https://www.news.cn/politics/7',
        normalized_url: 'https://www.news.cn/politics/7',
        status: 'stored',
        policy_id: 7,
        attempt_count: 1,
        reason_code: null,
        reason_message: null,
        started_at: '2026-07-31T04:00:00Z',
        finished_at: '2026-07-31T04:01:00Z',
      },
    ],
    total: 1,
    page: 1,
    page_size: 50,
  }
}

function taskLogs(): object {
  return {
    items: [{ id: 1, level: 'info', message: '采集完成', context: {}, created_at: '2026-07-31T04:01:00Z' }],
    total: 1,
    page: 1,
    page_size: 50,
  }
}

function analysisTask(id: number): object {
  return {
    id,
    task_type: 'word_frequency',
    status: 'succeeded',
    policy_count: 2,
    requested_by: 1,
    started_at: '2026-07-31T04:00:00Z',
    finished_at: '2026-07-31T04:01:00Z',
    error_summary: null,
    created_at: '2026-07-31T04:00:00Z',
  }
}

function analysisTaskPage(): object {
  return { items: [analysisTask(51)], total: 1, page: 1, page_size: 20 }
}

function analysisWords(): object {
  return {
    items: [
      { word: '人工智能', frequency: 12, tfidf: 0.92, doc_count: 2 },
      { word: '产业', frequency: 9, tfidf: 0.45, doc_count: 2 },
      { word: '数字经济', frequency: 6, tfidf: 0.7, doc_count: 1 },
    ],
    total: 3,
  }
}

function analysisRelations(): object {
  return {
    items: [{ word1: '产业', word2: '人工智能', co_count: 2 }],
    nodes: ['人工智能', '产业', '数字经济'],
  }
}

function analysisLogs(): object {
  return {
    items: [{ id: 1, level: 'info', message: '词频分析完成。', context: {}, created_at: '2026-07-31T04:01:00Z' }],
    total: 1,
    page: 1,
    page_size: 50,
  }
}
