import { render, screen } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SettingsView from '../../app/frontend/src/views/SettingsView.vue'

function jsonResponse(body: object): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

describe('系统配置页', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('完整嵌套配置按路径展示来源，并对敏感键前端兜底掩码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          values: {
            server: { port: 30080 },
            auth: { session_secret: 'real-secret-must-not-leak' },
            webfetch: { api_key: 'real-api-key-must-not-leak', base_url: 'http://fetch.internal' },
          },
          sources: {
            'server.port': 'config_file',
            'auth.session_secret': 'environment',
            'webfetch.api_key': 'environment',
            'webfetch.base_url': 'config_file',
          },
          webfetch: { status: 'ready', checked: true },
        }),
      ),
    )

    render(SettingsView, { global: { plugins: [createPinia()] } })

    expect(await screen.findByText('WebFetch 可用')).toBeInTheDocument()
    expect(screen.getByText('server.port')).toBeInTheDocument()
    expect(screen.getAllByText('环境变量').length).toBeGreaterThanOrEqual(2)
    const pageText = document.body.textContent || ''
    expect(pageText).not.toContain('real-secret-must-not-leak')
    expect(pageText).not.toContain('real-api-key-must-not-leak')
    expect(screen.getAllByText('********').length).toBeGreaterThanOrEqual(2)
  })
})
