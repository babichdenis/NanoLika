import qrcode from './vendor/qrcode.js'
import type { ChannelStart } from './api.ts'

export const BOT_LINKS = { telegram: 'https://t.me/nanolika_bot', max: 'https://max.ru/id420517592685_bot' } as const

export function connectionQR(start: ChannelStart): { path: string; size: number } {
  const expected = new URL(BOT_LINKS[start.channel])
  const url = new URL(start.url)
  const prefix = start.channel === 'telegram' ? 'TG' : 'MX'
  if (url.origin !== expected.origin || url.pathname !== expected.pathname || url.username || url.password || url.hash || [...url.searchParams].length !== 1 || !new RegExp('^nl_' + prefix + '[A-F0-9]{30}$').test(url.searchParams.get('start') ?? '')) throw new Error('Invalid connection link')
  const qr = qrcode(0, 'M')
  qr.addData(url.href, 'Byte')
  qr.make()
  const modules = qr.getModuleCount()
  let path = ''
  for (let row = 0; row < modules; row++) for (let col = 0; col < modules; col++) if (qr.isDark(row, col)) path += `M${col + 4} ${row + 4}h1v1h-1z`
  return { path, size: modules + 8 }
}
