import { fileURLToPath, URL } from 'node:url'
import { createRequire } from 'node:module'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

const require = createRequire(import.meta.url)

export default defineConfig({
  plugins: [vue()],
  server: {
    fs: {
      allow: [fileURLToPath(new URL('../../..', import.meta.url))],
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@testing-library/vue': require.resolve('@testing-library/vue'),
      pinia: require.resolve('pinia'),
      'vue-router': require.resolve('vue-router'),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['../../tests/frontend/**/*.spec.ts'],
    setupFiles: ['./src/tests/setup.ts'],
    clearMocks: true,
    restoreMocks: true,
  },
})
