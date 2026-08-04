type TaskLike = { status: string }
type LoadTask<T extends TaskLike> = () => Promise<T>

const TERMINAL_STATUSES = new Set(['succeeded', 'partially_succeeded', 'failed', 'cancelled'])

export function createTaskPolling<T extends TaskLike>(load: LoadTask<T>, intervalMs = 2000) {
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = true

  async function tick(): Promise<void> {
    if (stopped) return
    const task = await load()
    if (TERMINAL_STATUSES.has(task.status)) {
      stop()
      return
    }
    timer = setTimeout(() => void tick(), intervalMs)
  }

  async function start(): Promise<void> {
    stop()
    stopped = false
    await tick()
  }

  function stop(): void {
    stopped = true
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  return { start, stop }
}
