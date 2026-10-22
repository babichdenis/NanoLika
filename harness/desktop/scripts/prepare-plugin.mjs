#!/usr/bin/env node
// Rebuild and stage the plugin before packaging an installer. No dependency install.
import { cpSync, existsSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const plugin = resolve(desktop, '../dsh-nanomuse');
const staged = resolve(desktop, 'dsh/node_modules/dsh-nanomuse');
if (!existsSync(resolve(staged, 'package.json'))) throw new Error('Run prepare-dsh before packaging the desktop app');
const result = spawnSync(process.execPath, ['build.mjs'], { cwd: plugin, stdio: 'inherit' });
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);
for (const name of ['lib', 'assets', 'presets', 'package.json', 'cordis.patch.yml']) {
  rmSync(resolve(staged, name), { recursive: true, force: true });
  cpSync(resolve(plugin, name), resolve(staged, name), { recursive: true });
}
