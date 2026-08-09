import { expect, test } from '@playwright/test'

import { installMockApi } from './mock-api'

test('勾选政策并查看词频分析结果', async ({ page }) => {
  await installMockApi(page)
  await page.goto('/login')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('admin123')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page).toHaveURL(/\/policies/)
  await page.getByLabel('选择 中央政治局召开会议').check()
  await page.getByRole('button', { name: /分词分析/ }).click()
  await expect(page).toHaveURL(/\/analysis\?taskId=51/)
  await expect(page.getByText('任务 #51')).toBeVisible()
  await expect(page.getByRole('tab', { name: '词频排行' })).toBeVisible()
  await page.getByRole('tab', { name: '词云' }).click()
  await expect(page.getByRole('tab', { name: '词云' })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('tab', { name: '关键词关系图' }).click()
  await expect(page.getByRole('tab', { name: '关键词关系图' })).toHaveAttribute(
    'aria-selected',
    'true',
  )
})
