/**
 * The agent's voice out loud: `speak` reads an answer in the person's own
 * language with a voice the computer already has, so a conversation can be
 * heard instead of read. The listening half is dsh's voice input
 * (`dsh-experimental-speech-to-text`); this is the half that answers back.
 *
 * Nothing leaves the machine and no model is involved: macOS `say`, Windows'
 * own speech synthesizer, `spd-say` on Linux where speech-dispatcher is
 * installed. A relay voice of our own would cost money per word, so it stays a
 * later question (docs/roadmap.md).
 */
import { spawn } from 'node:child_process'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import z from '@deepseek-ai/schemastery'

export const name = 'nanomuse-voice'
export const inject = ['tools']

export interface Config {
  /** The system voice to use; empty means the system's own default. */
  voice: string
  /** Words per minute on macOS; ignored where the platform has its own rate. */
  rate: number
  /** How much of one answer may be read aloud: a spoken answer, not a document. */
  maxChars: number
}

export const Config: z<Config> = z.object({
  voice: z.string().default('').description("The system voice, for example 'Milena' for Russian; empty means the system's default voice."),
  rate: z.natural().default(180).description('Words per minute on macOS.'),
  maxChars: z.natural().default(600).description('The most characters one call reads aloud.'),
})

/** Markdown that would be read as punctuation: a voice should hear the words only. */
const MARKUP = /```[\s\S]*?```|`([^`]*)`|\[([^\]]+)\]\([^)]*\)|[*_#>]+/g

export function spoken(text: string, maxChars: number): string {
  const plain = text
    .replace(MARKUP, (_whole: string, code?: string, link?: string) => code ?? link ?? ' ')
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.!?;:])/g, '$1')
    .trim()
  if (plain.length <= maxChars) return plain
  const cut = plain.slice(0, maxChars)
  const stop = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('! '), cut.lastIndexOf('? '))
  return stop >= maxChars / 2 ? cut.slice(0, stop + 1) : cut
}

/** Start the platform's own voice on `text`; resolves when it has finished. */
function say(text: string, config: Config): Promise<void> {
  const voice = config.voice ? ['-v', config.voice] : []
  const rate = process.platform === 'darwin' ? ['-r', String(config.rate)] : []
  return new Promise<void>((resolve, reject) => {
    if (process.platform === 'win32') {
      void speakOnWindows(text, config).then(resolve, reject)
      return
    }
    const command = process.platform === 'darwin' ? 'say' : 'spd-say'
    const args = process.platform === 'darwin' ? [...voice, ...rate, '--', text] : ['--', text]
    const child = spawn(command, args, { stdio: 'ignore' })
    child.once('error', reject)
    child.once('close', (code) => (code === 0 ? resolve() : reject(new Error(`${command} exited with ${code ?? 'no code'}`))))
  })
}

/** Windows speaks from a file, so that quotes and apostrophes in the text stay text. */
async function speakOnWindows(text: string, config: Config): Promise<void> {
  const dir = await mkdtemp(join(tmpdir(), 'nanomuse-voice-'))
  const file = join(dir, 'say.txt')
  await writeFile(file, text, 'utf8')
  const script = [
    'Add-Type -AssemblyName System.Speech',
    '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer',
    ...(config.voice ? [`$s.SelectVoice('${config.voice.replace(/'/g, "''")}')`] : []),
    `$s.Rate = ${Math.round((config.rate - 180) / 30)}`,
    `$s.Speak([IO.File]::ReadAllText('${file.replace(/'/g, "''")}'))`,
  ].join('; ')
  await new Promise<void>((resolve, reject) => {
    const child = spawn('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], { stdio: 'ignore' })
    child.once('error', reject)
    child.once('close', (code) => (code === 0 ? resolve() : reject(new Error(`powershell exited with ${code ?? 'no code'}`))))
  })
}

export function apply(ctx: Context, config: Config): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'speak',
          description:
            'Read an answer out loud with this computer\'s own voice, in the language it is written in. Use it when the person asks to be told something rather than shown it, or in a spoken back and forth; keep it to the answer itself, not to a document, and never read code, links or a table aloud.',
          parameters: {
            text: { type: 'string', required: true, description: 'What to say, in the person\'s language. Markdown is dropped; the words are read as they are.' },
          },
          output: {
            schema: { type: 'object', properties: { spoken: { type: 'string' } }, additionalProperties: false },
            render: (_args, value) => [{ type: 'text', text: value.spoken ? 'Said it out loud.' : 'There was nothing to say.' }],
          },
          async execute(args) {
            const text = spoken(args.text, config.maxChars)
            if (!text) return { spoken: '' }
            await say(text, config)
            return { spoken: text.slice(0, 200) }
          },
          presentCall: () => ({ card: 'generic', title: 'Speak', kind: 'other', rawInput: {} }),
        }),
      ),
    'nanomuse voice: speak',
  )
}
