import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createTaskPolling } from '../../app/frontend/src/views/tasks/use-task-polling'

describe('任务轮询', () => {
  beforeEach(() => vi.useFakeTimers())

  it('运行态每 2 秒刷新，进入终态后停止', async () => {
    const load = vi.fn().mockResolvedValueOnce({ status: 'running' }).mockResolvedValueOnce({ status: 'succeeded' })
    const polling = createTaskPolling(load, 2000)
    await polling.start()
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(4000)
    expect(load).toHaveBeenCalledTimes(2)
    polling.stop()
    vi.useRealTimers()
  })
})
