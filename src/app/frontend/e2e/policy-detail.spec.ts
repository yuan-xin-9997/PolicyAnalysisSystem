import { expect, test } from '@playwright/test'

import { installMockApi } from './mock-api'

test('政策正文按段落分段显示并施加首行缩进', async ({ page }) => {
  await installMockApi(page)
  await page.goto('/login')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('admin123')
  await page.getByRole('button', { name: '登录' }).click()

  await page.goto('/policies/7')
  await expect(page.getByRole('region', { name: '政策正文' })).toBeVisible()

  const paragraphs = page.locator('.policy-content p')
  await expect(paragraphs).toHaveCount(3)
  await expect(paragraphs.nth(0)).toHaveText('第一段政策正文。')
  await expect(paragraphs.nth(1)).toHaveText('第二段政策正文，用于验证分段与首行缩进。')

  const { indent, fontSize } = await paragraphs.first().evaluate((el) => {
    const style = window.getComputedStyle(el)
    return { indent: parseFloat(style.textIndent), fontSize: parseFloat(style.fontSize) }
  })
  // text-indent: 2em resolves to twice the paragraph font-size.
  expect(indent).toBeGreaterThan(0)
  expect(indent).toBeCloseTo(fontSize * 2, 0)

  await expect(page.locator('.policy-content script')).toHaveCount(0)
  expect(await page.evaluate(() => 'e2eLeak' in window)).toBe(false)
})
