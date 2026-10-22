#!/usr/bin/env node
// Backport the ASF size guard without replacing Jimp's CommonJS file-type API.
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
const require = createRequire(import.meta.url);
const core = join(dirname(require.resolve('file-type')), 'core.js');
const source = readFileSync(core, 'utf8');
const marker = '// NanoLika: reject non-progressing ASF objects (GHSA-5v7r-6r5c-r473).';
if (!source.includes(marker)) {
  const line = 'let payload = header.size - 24;';
  if (!source.includes(line)) throw new Error('file-type parser changed; review the ASF safety backport before packaging');
  writeFileSync(core, source.replace(line, `${marker}\n\t\t\tif (!Number.isSafeInteger(header.size) || header.size < 24 || header.size > tokenizer.fileInfo.size - tokenizer.position + 24) return;\n\t\t\t${line}`));
}
