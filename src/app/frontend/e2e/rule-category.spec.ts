import { expect, test } from '@playwright/test'

import { installMockApi } from './mock-api'

test('采集规则可直接新建中央委员会全会类别并复用', async ({ page }) => {
  await installMockApi(page)
  await page.route('**/api/v1/collection-rules', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const body = route.request().postDataJSON()
    expect(body.category_name).toBe('中央委员会全会')
    expect(body.category_code).toBeUndefined()
    await route.fulfill({ json: {
      ...body, id: 10,
      source: { code: 'xinhua', name: '新华网' },
      category: { id: 2, code: 'custom_plenum', name: body.category_name, is_active: true },
      updated_at: '2026-10-29T12:00:00Z',
    }, status: 201 })
  })
  await page.goto('/login')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('admin123')
  await page.getByRole('button', { name: '登录' }).click()
  await page.goto('/tasks/rules')
  await page.getByLabel('规则名称').fill('全会采集')
  await page.getByRole('combobox', { name: /^来源/ }).selectOption('xinhua')
  await page.getByRole('combobox', { name: /^类别/ }).selectOption('__new__')
  await page.getByLabel('包含词').fill('中央委员会全体会议')
  await page.getByLabel('RSS URL').fill('https://www.news.cn/rss.xml')
  await page.getByRole('button', { name: '保存规则' }).click()
  await expect(page.getByRole('alert')).toContainText('请填写新类别名称')
  await page.getByLabel('新类别名称').fill('中央委员会全会')
  await page.getByRole('button', { name: '保存规则' }).click()
  await expect(page.getByRole('cell', { name: '中央委员会全会', exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: /^类别/ }).selectOption('custom_plenum')
  await expect(page.getByLabel('新类别名称')).toHaveCount(0)
})

