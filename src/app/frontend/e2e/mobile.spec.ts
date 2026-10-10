import { expect, test } from '@playwright/test'

import { installMockApi } from './mock-api'

for (const width of [320, 390, 768]) {
  test(`移动布局在 ${width}px 视口可导航且不产生页面级横向滚动`, async ({ page }) => {
    await page.setViewportSize({ width, height: 760 })
    await installMockApi(page)
    await page.goto('/login')

    await expect(page.locator('html')).toHaveJSProperty('scrollWidth', width)
    await page.getByLabel('用户名').fill('admin')
    await page.getByLabel('密码').fill('admin123')
    await page.getByRole('button', { name: '登录' }).click()

    await expect(page.getByRole('button', { name: '打开导航菜单' })).toBeVisible()
    await page.getByRole('button', { name: '打开导航菜单' }).click()
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
    await page.getByRole('link', { name: /任务中心/ }).click()
    await expect(page).toHaveURL(/\/tasks$/)
    await expect(page.getByRole('button', { name: '打开导航菜单' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )

    const documentOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(documentOverflow).toBeLessThanOrEqual(1)

    const table = page.getByRole('table')
    await expect(table).toBeVisible()
    await expect(table.locator('xpath=..')).toHaveClass(/table-wrap/)
    if (width <= 390) {
      expect(await table.evaluate((element) => element.scrollWidth)).toBeGreaterThan(width)
    }

    const filterButtonHeight = await page
      .getByRole('button', { name: '筛选', exact: true })
      .evaluate((element) => element.getBoundingClientRect().height)
    expect(filterButtonHeight).toBeGreaterThanOrEqual(44)
  })
}
