import { expect, test } from '@playwright/test'

import { installMockApi } from './mock-api'

test('政策检索、纯文本详情、手工任务和任务详情主路径可用', async ({ page }) => {
  await installMockApi(page)
  await page.goto('/login')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('admin123')
  await page.getByRole('button', { name: '登录' }).click()

  await page.getByLabel('标题关键词').fill('政治局')
  await page.getByLabel('正文全文检索').fill('政策正文')
  await page.getByLabel('发布部门').selectOption('新华社')
  await page.getByLabel('政策类别').selectOption('1')
  await page.getByLabel('政策来源').selectOption('1')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page).toHaveURL(/full_text=%E6%94%BF%E7%AD%96%E6%AD%A3%E6%96%87/)
  await expect(page.getByText('降序（最新优先）')).toBeVisible()
  await page.getByRole('button', { name: /最近抓取时间.*点击切换为降序/ }).click()
  await expect(page).toHaveURL(/sort_by=last_crawled_at/)
  await page.getByRole('link', { name: '中央政治局召开会议' }).click()
  await expect(page.getByRole('region', { name: '政策正文' })).toBeVisible()
  await expect(page.getByText('第一段政策正文。')).toBeVisible()
  await expect(page.locator('.policy-content script')).toHaveCount(0)
  await expect(page.locator('.policy-content')).toContainText('<script>window.e2eLeak=true</script>')
  expect(await page.evaluate(() => 'e2eLeak' in window)).toBe(false)

  await page.getByRole('link', { name: /任务中心/ }).click()
  await page.getByLabel('采集规则').selectOption('9')
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '触发采集' }).click()
  await expect(page).toHaveURL(/\/tasks\/42$/)
  await expect(page.getByRole('heading', { name: '采集任务详情' })).toBeVisible()
  await expect(page.getByText('采集完成')).toBeVisible()
})
