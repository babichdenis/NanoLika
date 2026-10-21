// The right-click on a Workspace in the harness's own sidebar: the row the core marks with
// `data-row-key="workspace:<id>"` is the handle, and the two actions are the core's own
// rename and delete, so no DOM class name (which its CSS modules hash per build) is involved.
import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const here = fileURLToPath(new URL('.', import.meta.url))
let dir
let row

/** One self-contained bundle of the two helpers, so the test needs no DOM and no client bundle. */
before(async () => {
  dir = await mkdtemp(join(tmpdir(), 'nm-workspace-row-'))
  await build({
    entryPoints: [join(here, '..', 'src/client/workspace-row.ts')],
    outfile: join(dir, 'workspace-row.mjs'),
    format: 'esm',
    platform: 'node',
    target: 'node22',
    bundle: true,
    logLevel: 'silent',
  })
  row = await import(pathToFileURL(join(dir, 'workspace-row.mjs')).href)
})

after(async () => {
  if (dir) await rm(dir, { recursive: true, force: true })
})

const rowOf = (key) => ({ getAttribute: (name) => (name === 'data-row-key' ? key : null) })

test('a Workspace row is read from its key, and nothing else is', () => {
  assert.equal(row.WORKSPACE_ROW_PREFIX, 'workspace:')
  const hit = row.workspaceOfRow(rowOf('workspace:0d905435-0c61-4ad0-bc64-ccb23b7e93c1'))
  assert.equal(hit?.id, '0d905435-0c61-4ad0-bc64-ccb23b7e93c1')
  // a chat row carries a session, not a workspace
  assert.equal(row.workspaceOfRow(rowOf('session:abc')), null)
  // no key, an empty id, a row that is not there at all
  assert.equal(row.workspaceOfRow(rowOf('')), null)
  assert.equal(row.workspaceOfRow(rowOf('workspace:')), null)
  assert.equal(row.workspaceOfRow(rowOf(undefined)), null)
  assert.equal(row.workspaceOfRow(null), null)
})

test('a right-click finds the Workspace it landed in, through whatever element it hit', () => {
  class FakeElement {
    constructor(key, parent = null) { this.key = key; this.parent = parent }
    getAttribute(name) { return name === 'data-row-key' ? this.key : null }
    closest(selector) {
      // the one selector the menu asks for: a Workspace group, by its key prefix
      assert.equal(selector, `[data-row-key^="${row.WORKSPACE_ROW_PREFIX}"]`)
      for (let node = this; node !== null; node = node.parent) {
        if ((node.key ?? '').startsWith(row.WORKSPACE_ROW_PREFIX)) return node
      }
      return null
    }
  }
  const previous = globalThis.Element
  globalThis.Element = FakeElement
  try {
    const group = new FakeElement('workspace:abc')
    const label = new FakeElement(undefined, group)
    const hit = row.workspaceIdOf(label)
    assert.equal(hit?.id, 'abc')
    assert.equal(hit?.row, group)
    // a press on a chat row, and a target that is not an element at all
    assert.equal(row.workspaceIdOf(new FakeElement('session:s1')), null)
    assert.equal(row.workspaceIdOf(null), null)
    assert.equal(row.workspaceIdOf('workspace:abc'), null)
    // a window without Element (a worker, an older test runner) says no rather than throwing
    delete globalThis.Element
    assert.equal(row.workspaceIdOf(label), null)
  } finally {
    globalThis.Element = previous
  }
})

test('the sidebar mounts the menu, and the client entry gives it the core service', async () => {
  const sidebar = await readFile(new URL('../src/client/MuseSidebar.tsx', import.meta.url), 'utf8')
  assert.match(sidebar, /import \{ WorkspaceRowMenu \} from '\.\/WorkspaceRowMenu\.tsx'/)
  assert.match(sidebar, /workspaceAdmin \? h\(WorkspaceRowMenu, \{ t, admin: workspaceAdmin \}\)/)
  const entry = await readFile(new URL('../src/client/index.ts', import.meta.url), 'utf8')
  // the two writes are the core's own: rename keeps the title, delete takes the folder off the list
  assert.match(entry, /rename\(workspaceId: string, title: string\): Promise<unknown>/)
  assert.match(entry, /delete\(workspaceId: string\): Promise<void>/)
  assert.match(entry, /workspaceAdmin: \{/)
  // the menu listens for the right-click itself and stops the browser's own menu
  const menu = await readFile(new URL('../src/client/WorkspaceRowMenu.tsx', import.meta.url), 'utf8')
  assert.match(menu, /document\.addEventListener\('contextmenu', onContextMenu, true\)/)
  assert.match(menu, /event\.preventDefault\(\)/)
  assert.match(menu, /t\('wsRename'\)/)
  assert.match(menu, /t\('wsClose'\)/)
})
