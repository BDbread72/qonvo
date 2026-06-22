import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// 단일 진실원: 루트 build.toml 의 [app] version 을 빌드 시 읽어 주입한다.
// 출시 때 build.toml 만 올리면 사이트 버전 표기도 자동으로 따라간다.
function appVersion() {
  try {
    const toml = readFileSync(fileURLToPath(new URL('../build.toml', import.meta.url)), 'utf-8')
    const m = toml.match(/^\s*version\s*=\s*["']([^"']+)["']/m)
    return m ? m[1] : 'dev'
  } catch {
    return 'dev'
  }
}

// Relative base ('./') keeps the build portable for static serving from any
// path or subdomain on the home server (no router / no history API in use).
export default defineConfig({
  base: './',
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion()),
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
})
