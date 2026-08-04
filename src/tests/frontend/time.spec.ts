import { describe, expect, it } from 'vitest'

import { formatBeijingTime } from '../../app/frontend/src/utils/time'

describe('formatBeijingTime', () => {
  it('把 UTC 时间统一显示为北京时间', () => {
    expect(formatBeijingTime('2026-07-31T04:30:00Z')).toBe('2026-07-31 12:30:00')
  })

  it('空值和非法时间显示占位符', () => {
    expect(formatBeijingTime(null)).toBe('—')
    expect(formatBeijingTime('not-a-date')).toBe('—')
  })
})
