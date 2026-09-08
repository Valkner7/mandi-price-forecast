import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
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
