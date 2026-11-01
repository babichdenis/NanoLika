// esbuild comes from the sibling bundle's install: this package has no dependencies of its own
import { build } from '../dsh-nanomuse/node_modules/esbuild/lib/main.js'
import { readFileSync } from 'node:fs'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
const PLATFORM_MODULES = [
  'react', 'react/jsx-runtime', 'react-dom', 'react-dom/client', '@deepseek-ai/cordis',
  '@deepseek-ai/dsh-client-store',
  '@deepseek-ai/dsh-client-ui-slots',
  '@deepseek-ai/dsh-client-ui-primitives',
  '@deepseek-ai/dsh-client-ui-dockkit',
]

await build({
  entryPoints: ['src/index.ts'],
  outdir: 'lib',
  format: 'esm',
  platform: 'node',
  target: 'node22',
  bundle: true,
  sourcemap: true,
  packages: 'external',
  logLevel: 'warning',
})

await build({
  entryPoints: { client: 'src/client.ts' },
  outdir: 'lib',
  format: 'cjs',
  platform: 'browser',
  target: 'es2022',
  bundle: true,
  minify: false,
  sourcemap: true,
  external: PLATFORM_MODULES,
  jsx: 'automatic',
  define: { 'process.env.NODE_ENV': '"production"' },
  banner: { js: `window.__ModuleLoader__.load({ id: ${JSON.stringify(pkg.name)}, factory: (require) => {\nvar module = { exports: {} }; var exports = module.exports;` },
  footer: { js: 'return module.exports; } });' },
  logLevel: 'warning',
})

console.log('nanomuse-listen: lib/index.js lib/client.js')
