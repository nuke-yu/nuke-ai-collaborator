import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test/setup.js",
  },
  server: {
    proxy: {
      // 127.0.0.1 (not "localhost"): pin to the LOCAL backend on IPv4. "localhost"
      // resolves to IPv6 ::1 first on macOS, which would hit a Docker container if
      // one is also bound to :8000 — keeping the local dev stack fully isolated.
      '/api': 'http://127.0.0.1:8000',
      '/uploads': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    }
  }
})
