import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/chat': 'http://agent.local',
      '/internships': 'http://agent.local',
      '/leetcode': 'http://agent.local',
      '/healthz': 'http://agent.local',
    },
  },
})
