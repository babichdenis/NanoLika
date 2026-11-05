import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const desktop = fileURLToPath(new URL('../', import.meta.url))
test('the Windows renderer fits every real tip in all three languages and DPI scales', { skip: process.platform !== 'win32' }, (t) => {
  const built = spawnSync(process.execPath, ['scripts/prepare-installer.mjs'], { cwd: desktop, encoding: 'utf8', timeout: 60_000 })
  assert.equal(built.status, 0, built.stdout + built.stderr)
  const output = mkdtempSync(join(tmpdir(), 'nanolika-tips-render-'))
  t.after(() => rmSync(output, { recursive: true, force: true }))
  const render = spawnSync(join(desktop, 'resources/installer-generated/NanoLika.SetupTips.exe'), ['--render-check', output], { windowsHide: true, timeout: 30_000 })
  assert.equal(render.status, 0, existsSync(join(output, 'failure.json')) ? readFileSync(join(output, 'failure.json'), 'utf8') : 'renderer exited without its report')
  const result = JSON.parse(readFileSync(join(output, 'result.json'), 'utf8'))
  assert.deepEqual(result, { locales: 3, scales: 3, widths: 2, slides: 8, passed: true })
})


function find(root, name, accept = () => true) {
  if (!existsSync(root)) return undefined
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (entry.isFile() && entry.name === name && accept(path)) return path
    if (entry.isDirectory()) { const match = find(path, name, accept); if (match) return match }
  }
}
const quote = (value) => value.replace(/\$/g, '$$$$').replace(/"/g, '$\\"')

test('the real MUI welcome, progress and finish pages link with the offline panel', { skip: process.platform !== 'win32' }, (t) => {
  const cache = join(process.env.LOCALAPPDATA, 'electron-builder', 'Cache')
  const compiler = find(join(cache, 'nsis-3.0.4.1'), 'makensis.exe')
  const plugin = find(join(cache, 'nsis-resources-3.4.1'), 'StdUtils.dll', (p) => p.includes('x86-unicode'))
  if (!compiler || !plugin) {
    if (process.env.NANOLIKA_REQUIRE_NSIS_TESTS === '1') assert.fail('NSIS must be cached by packaging first')
    t.skip('NSIS is fetched by the package step'); return
  }
  const dir = mkdtempSync(join(tmpdir(), 'nanolika-tips-link-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const source = join(dir, 'check.nsi')
  // Compile only. There is no app payload, upgrade, process stop or user interaction.
  writeFileSync(source, `Unicode true
RequestExecutionLevel user
Name "NanoLika page link check"
OutFile "${quote(join(dir, 'check.exe'))}"
!include "MUI2.nsh"
!addplugindir /x86-unicode "${quote(dirname(plugin))}"
!include "${quote(join(desktop, 'node_modules/app-builder-lib/templates/nsis/include/StdUtils.nsh'))}"
!macro _isUpdated _a _b _t _f
  \${StdUtils.TestParameter} $R9 "updated"
  StrCmp "$R9" "true" \`\${_t}\` \`\${_f}\`
!macroend
!define isUpdated \`"" isUpdated ""\`
Var launchLink
!include "${quote(join(desktop, 'resources/installer.nsh'))}"
!insertmacro customWelcomePage
!insertmacro customPageAfterChangeDir
!insertmacro MUI_PAGE_INSTFILES
!insertmacro customFinishPage
!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro customHeader
Section
SectionEnd
`, 'utf8')
  const result = spawnSync(compiler, ['/V2', '/INPUTCHARSET', 'UTF8', source], { encoding: 'utf8', timeout: 30_000 })
  assert.equal(result.status, 0, result.stdout + result.stderr)
})
