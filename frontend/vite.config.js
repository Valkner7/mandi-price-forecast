import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // README says "npm run build outputs to static/dashboard/", but that
    // wasn't actually configured anywhere -- the default outDir is
    // frontend/dist/, so a fresh build never reached the folder app.py
    // actually serves. Pointing outDir here directly makes the README
    // claim true and removes the manual-copy step that was previously
    // needed (and easy to forget).
    outDir: '../static/dashboard',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/meta': 'http://127.0.0.1:8000',
      '/predict': 'http://127.0.0.1:8000',
      '/history': 'http://127.0.0.1:8000',
      '/trends': 'http://127.0.0.1:8000',
      '/anomalies': 'http://127.0.0.1:8000',
      '/compare': 'http://127.0.0.1:8000',
      '/compare-advisory': 'http://127.0.0.1:8000',
      '/advisory': 'http://127.0.0.1:8000',
      '/voice-advisory': 'http://127.0.0.1:8000',
      '/sms': 'http://127.0.0.1:8000',
      '/whatsapp': 'http://127.0.0.1:8000',
      '/check-alerts': 'http://127.0.0.1:8000',
      '/api': 'http://127.0.0.1:8000',
      '/voice-test': 'http://127.0.0.1:8000',
      '/trends-dashboard': 'http://127.0.0.1:8000',
      '/docs': 'http://127.0.0.1:8000',
      '/redoc': 'http://127.0.0.1:8000',
      '/openapi.json': 'http://127.0.0.1:8000',
    },
  },
})
