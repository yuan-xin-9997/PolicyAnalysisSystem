import { expect, test } from '@playwright/test'

import { installMockApi } from './mock-api'

test('管理员登录后可见全部导航并能退出', async ({ page }) => {
  await installMockApi(page)
  await page.goto('/login')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('admin123')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page.getByRole('link', { name: /政策数据库/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /权限管理/ })).toBeVisible()
  await expect(page.getByText('v0.e2e')).toBeVisible()

  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login$/)
})
