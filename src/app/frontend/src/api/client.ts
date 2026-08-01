export interface ApiErrorBody {
  code: string
  message: string
  request_id: string
  details: Record<string, unknown>
}

interface ApiErrorEnvelope {
  error?: Partial<ApiErrorBody>
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string
  readonly details: Record<string, unknown>

  constructor(status: number, body: Partial<ApiErrorBody> = {}) {
    super(body.message || '请求失败，请稍后重试。')
    this.name = 'ApiError'
    this.status = status
    this.code = body.code || 'HTTP_ERROR'
    this.requestId = body.request_id || ''
    this.details = body.details || {}
  }
}

type CsrfTokenProvider = () => string | undefined
type UnauthorizedHandler = () => void | Promise<void>
const API_PREFIX = '/api/v1'
const URL_VALIDATION_ORIGIN = 'https://policy-api.invalid'

let csrfTokenProvider: CsrfTokenProvider = () => undefined
let unauthorizedHandler: UnauthorizedHandler = () => undefined

export function setCsrfTokenProvider(provider: CsrfTokenProvider): void {
  csrfTokenProvider = provider
}

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  unauthorizedHandler = handler
}

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const normalizedPath = normalizeApiPath(path)

  const method = (options.method || 'GET').toUpperCase()
  const headers = new Headers(options.headers)
  headers.delete('X-CSRF-Token')
  if (options.body != null && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (isMutation(method) && normalizedPath.pathname !== '/auth/login') {
    const csrfToken = csrfTokenProvider()
    if (csrfToken) {
      headers.set('X-CSRF-Token', csrfToken)
    }
  }

  const response = await fetch(`${API_PREFIX}${normalizedPath.requestPath}`, {
    ...options,
    method,
    headers,
    credentials: 'include',
  })
  const payload = await parseResponse(response)

  if (!response.ok) {
    const envelope = isRecord(payload) ? (payload as ApiErrorEnvelope) : {}
    const error = new ApiError(response.status, envelope.error)
    if (response.status === 401) {
      try {
        void Promise.resolve(unauthorizedHandler()).catch(() => undefined)
      } catch {
        // Authentication cleanup must not replace the safe API error.
      }
    }
    throw error
  }

  return payload as T
}

interface NormalizedApiPath {
  requestPath: string
  pathname: string
}

function normalizeApiPath(path: string): NormalizedApiPath {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('#')) {
    throw new TypeError('API path must be an application-relative path')
  }
  const queryIndex = path.indexOf('?')
  const rawPathname = queryIndex >= 0 ? path.slice(0, queryIndex) : path
  const query = queryIndex >= 0 ? path.slice(queryIndex) : ''
  let decodedPathname = rawPathname

  for (let depth = 0; depth < 5; depth += 1) {
    validateDecodedPath(decodedPathname)
    let next: string
    try {
      next = decodeURIComponent(decodedPathname)
    } catch {
      throw new TypeError('API path contains invalid encoding')
    }
    if (next === decodedPathname) {
      const normalizedUrl = new URL(
        `${API_PREFIX}${rawPathname}${query}`,
        URL_VALIDATION_ORIGIN,
      )
      if (
        normalizedUrl.origin !== URL_VALIDATION_ORIGIN ||
        !normalizedUrl.pathname.startsWith(`${API_PREFIX}/`)
      ) {
        throw new TypeError('API path escapes the application API prefix')
      }
      return {
        requestPath: `${normalizedUrl.pathname.slice(API_PREFIX.length)}${normalizedUrl.search}`,
        pathname: decodedPathname,
      }
    }
    decodedPathname = next
  }

  throw new TypeError('API path contains excessive encoding')
}

function validateDecodedPath(pathname: string): void {
  if (
    !pathname.startsWith('/') ||
    pathname.startsWith('//') ||
    Array.from(pathname).some((character) => {
      const codePoint = character.codePointAt(0) || 0
      return character === '\\' || codePoint < 32 || codePoint === 127
    }) ||
    pathname.split('/').some((segment) => segment === '.' || segment === '..')
  ) {
    throw new TypeError('API path escapes the application API prefix')
  }
}

function isMutation(method: string): boolean {
  return ['POST', 'PATCH', 'PUT', 'DELETE'].includes(method)
}

async function parseResponse(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined
  const text = await response.text()
  if (!text) return undefined
  try {
    return JSON.parse(text) as unknown
  } catch {
    return undefined
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
