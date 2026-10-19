/** Render code-native SVG brand assets using a locally installed Sharp. No downloads. */
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import { dirname } from 'node:path'
const require = createRequire(import.meta.url)
const sharp = require(process.env.NANOLIKA_SHARP_MODULE || 'sharp')
let input = ''
for await (const chunk of process.stdin) input += chunk
for (const { path, svg, size } of JSON.parse(input)) {
  await mkdir(dirname(path), { recursive: true })
  await sharp(Buffer.from(svg)).resize({ width: size }).png().toFile(path)
}
