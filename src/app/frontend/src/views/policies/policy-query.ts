import type { LocationQuery } from 'vue-router'

export interface PolicyQueryForm {
  keyword: string
  fullText: string
  publisher: string
  categoryId: string
  sourceId: string
  publishedFrom: string
  publishedTo: string
  sortBy: 'published_at' | 'last_crawled_at'
  sortOrder: 'asc' | 'desc'
  page: number
  pageSize: number
}

export function defaultPolicyQuery(): PolicyQueryForm {
  return {
    keyword: '',
    fullText: '',
    publisher: '',
    categoryId: '',
    sourceId: '',
    publishedFrom: '',
    publishedTo: '',
    sortBy: 'published_at',
    sortOrder: 'desc',
    page: 1,
    pageSize: 20,
  }
}

export function toPolicyApiQuery(form: PolicyQueryForm): Record<string, string | number> {
  const query: Record<string, string | number> = {
    page: form.page,
    page_size: form.pageSize,
    sort_by: form.sortBy,
    sort_order: form.sortOrder,
  }
  if (form.keyword.trim()) query.keyword = form.keyword.trim()
  if (form.fullText.trim()) query.full_text = form.fullText.trim()
  if (form.publisher.trim()) query.publisher = form.publisher.trim()
  if (form.categoryId) query.category_id = form.categoryId
  if (form.sourceId) query.source_id = form.sourceId
  if (form.publishedFrom) query.published_from = `${form.publishedFrom}T00:00:00+08:00`
  if (form.publishedTo) query.published_to = `${form.publishedTo}T23:59:59+08:00`
  return query
}

export function fromRouteQuery(query: LocationQuery): PolicyQueryForm {
  const form = defaultPolicyQuery()
  form.keyword = first(query.keyword)
  form.fullText = first(query.full_text)
  form.publisher = first(query.publisher)
  form.categoryId = first(query.category_id)
  form.sourceId = first(query.source_id)
  form.publishedFrom = first(query.published_from).slice(0, 10)
  form.publishedTo = first(query.published_to).slice(0, 10)
  form.sortBy = first(query.sort_by) === 'last_crawled_at' ? 'last_crawled_at' : 'published_at'
  form.sortOrder = first(query.sort_order) === 'asc' ? 'asc' : 'desc'
  form.page = positiveInteger(first(query.page), 1)
  form.pageSize = positiveInteger(first(query.page_size), 20)
  return form
}

function first(value: LocationQuery[string]): string {
  return Array.isArray(value) ? value[0] || '' : value || ''
}

function positiveInteger(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}
