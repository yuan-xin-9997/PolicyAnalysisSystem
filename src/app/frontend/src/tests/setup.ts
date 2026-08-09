import { afterEach, vi } from 'vitest'

vi.mock('echarts', () => ({
  init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
  use: vi.fn(),
}))
vi.mock('echarts-wordcloud', () => ({}))

import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/vue'

afterEach(() => {
  cleanup()
  sessionStorage.clear()
})
