#!/usr/bin/env node
// The optional Windows illustration panel uses the OS's existing .NET Framework.
// Compile from source on Windows; never add a downloaded installer plugin or runtime.
import { existsSync, mkdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
if (process.platform === 'win32') {
  const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const compiler = join(process.env.WINDIR || 'C:\\Windows', 'Microsoft.NET', 'Framework', 'v4.0.30319', 'csc.exe')
  if (!existsSync(compiler)) throw new Error('The Windows .NET Framework compiler is required to build SetupTips')
  const output = join(desktop, 'resources', 'installer-generated')
  mkdirSync(output, { recursive: true })
  const args = ['/nologo', '/target:winexe', '/platform:anycpu', '/optimize+', '/codepage:65001', '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll',
    '/win32manifest:' + join(desktop, 'installer', 'SetupTips.manifest'),
    '/win32icon:' + join(desktop, 'resources', 'app-icon.ico'),
    '/out:' + join(output, 'NanoLika.SetupTips.exe'),
    '/resource:' + join(desktop, 'installer', 'slides.json') + ',NanoLika.Slides',
    ...['working','idle','waiting','happy'].map((name) => '/resource:' + join(desktop, 'resources', 'installer-slides', name + '.png') + ',NanoLika.' + name),
    join(desktop, 'installer', 'SetupTips.cs')]
  const result = spawnSync(compiler, args, { stdio: 'inherit', windowsHide: true })
  if (result.status !== 0) throw new Error('SetupTips compilation failed')
  console.log('installer: built the offline Lika tips panel')
}
