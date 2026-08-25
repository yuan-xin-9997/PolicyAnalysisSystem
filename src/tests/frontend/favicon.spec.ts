import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const frontendRoot = resolve(__dirname, '../../app/frontend')

describe('浏览器标签页图标', () => {
  it('在 HTML 入口声明可打包的 favicon', () => {
    const html = readFileSync(resolve(frontendRoot, 'index.html'), 'utf-8')
    const faviconHref = '/favicon.svg'

    expect(html).toContain(`rel="icon"`)
    expect(html).toContain(`href="${faviconHref}"`)
    expect(existsSync(resolve(frontendRoot, 'public', faviconHref.slice(1)))).toBe(true)
  })
})
