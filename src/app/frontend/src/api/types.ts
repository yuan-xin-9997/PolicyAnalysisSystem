export interface ApiErrorBody {
  code: string
  message: string
  request_id: string
  details: Record<string, unknown>
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export type UserRole = 'admin' | 'user'
export type PageCode = 'policies' | 'tasks' | 'push' | 'analysis' | 'users' | 'settings'

export interface CurrentUser {
  id: number
  username: string
  role: UserRole
  page_permissions: PageCode[]
}

export interface PolicySummary {
  id: number
  title: string
  canonical_url: string
  publisher: string
  category: PolicyReference
  source: PolicyReference
  published_at: string
  first_crawled_at: string
  last_crawled_at: string
  content_hash: string
  latest_task_id: number | null
}

export interface PolicyDetail extends PolicySummary {
  content_text: string
}

export interface PolicyReference {
  id: number
  code: string
  name: string
}

export interface PolicyFilterOptions {
  publishers: string[]
  categories: PolicyReference[]
  sources: PolicyReference[]
}

export type TaskStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'partially_succeeded'
  | 'failed'
  | 'cancelled'

export interface CrawlTask {
  id: number
  rule_id: number
  trigger_type: 'manual' | 'schedule'
  status: TaskStatus
  requested_by: number | null
  scheduled_for: string | null
  started_at: string | null
  finished_at: string | null
  cancel_requested_at: string | null
  error_summary: string | null
  progress: { processed: number; discovered: number }
  counts: {
    success: number
    duplicate: number
    filtered: number
    failed: number
    total_terminal_items: number
  }
}

export type TaskItemStatus = 'stored' | 'updated' | 'duplicate' | 'filtered' | 'failed'

export interface CrawlTaskItem {
  id: number
  candidate_url: string
  normalized_url: string | null
  status: TaskItemStatus
  policy_id: number | null
  attempt_count: number
  reason_code: string | null
  reason_message: string | null
  started_at: string | null
  finished_at: string | null
}

export interface TaskLog {
  id: number
  level: 'debug' | 'info' | 'warning' | 'error'
  message: string
  context: Record<string, unknown>
  created_at: string
}

export interface SourceSummary {
  id: number
  code: string
  name: string
  organization: string
  base_url: string
  adapter_type: string
  allowed_domains: string[]
  is_active: boolean
}

export interface PolicyCategory {
  id: number
  code: string
  name: string
  description: string | null
  is_active: boolean
}

export interface CollectionRule {
  id: number
  name: string
  source: SourceSummary
  category: PolicyCategory
  include_keywords: string[]
  exclude_keywords: string[]
  history_years: number
  discovery: { rss_urls: string[]; channel_urls: string[] }
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface Schedule {
  id: number
  rule_id: number
  rule_name: string
  cron_expression: string
  timezone: 'Asia/Shanghai'
  is_active: boolean
  next_run_at: string | null
  last_run_at: string | null
}

export interface EffectiveSettings {
  values: Record<string, unknown>
  sources: Record<string, string>
  webfetch: { status: 'ready' | 'unavailable' | 'configured' | 'not_configured'; checked: boolean }
}

export type AnalysisTaskStatus = 'pending' | 'running' | 'succeeded' | 'failed'

export interface AnalysisTaskSummary {
  id: number
  task_type: string
  status: AnalysisTaskStatus
  policy_count: number
  requested_by: number | null
  started_at: string | null
  finished_at: string | null
  error_summary: string | null
  created_at: string
}

export interface AnalysisTaskPage {
  items: AnalysisTaskSummary[]
  total: number
  page: number
  page_size: number
}

export interface CreateAnalysisTaskResponse {
  task_id: number
  status: AnalysisTaskStatus
}

export interface WordFrequencyItem {
  word: string
  frequency: number
  tfidf: number
  doc_count: number
}

export interface WordFrequencyResult {
  items: WordFrequencyItem[]
  total: number
}

export interface WordRelationItem {
  word1: string
  word2: string
  co_count: number
}

export interface WordRelationResult {
  items: WordRelationItem[]
  nodes: string[]
}

export interface AnalysisTaskLogItem {
  id: number
  level: 'debug' | 'info' | 'warning' | 'error'
  message: string
  context: Record<string, unknown>
  created_at: string
}

export interface AnalysisTaskLogPage {
  items: AnalysisTaskLogItem[]
  total: number
  page: number
  page_size: number
}
