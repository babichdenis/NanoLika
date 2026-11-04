/**
 * The catalog's index: what the request does not carry, found by asking.
 *
 * A session sends every registered tool to the model on every step. Thirty-eight of them
 * (ours plus the MCP ones) cost more than the whole system prompt, and most are not used
 * in a given turn. So the tools whose names are in {@link DEFERRED} are not registered at
 * startup: they live in a catalog here, and `tool_search` registers the ones a question is
 * about. The harness already supports a tool list that changes between steps (it diffs the
 * names and sends the additions), so the found tool is callable at once.
 *
 * The idea is mcp-toolsearch-proxy's (datolabs, MIT) applied inside our plugin: one search
 * tool instead of a catalog, and the same Russian words that make the Python runtime's
 * `tool_search` work (nanomuse/tools/discovery.py).
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool, type ToolDefinition } from '@deepseek-ai/dsh-tools'

export const name = 'nanomuse-discovery'
export const inject = ['tools']

/** Tools that stay in every request: the ones a turn nearly always needs. */
export const CORE = new Set([
  'make_image',
  'speak',
  'remember',
  'memory_recall',
  'conversation_open',
  'device_screen',
  'preview_show',
  'devices',
])

/** What a person says when they want this job, in Russian. Matching folds case and
 *  endings; a tool missing here is still found by its own name and description. */
const WORDS: Record<string, string> = {
  goals_room_update: 'цель цели обновить прогресс отметка по цели goal',
  feed_post: 'лента пост запись в ленту feed',
  library_add: 'склад библиотека файл сохранить в библиотеку library',
  draw_new_look: 'нарисовать образ аватар лицо внешность avatar look draw',
  make_music: 'музыка трек песня мелодия music track',
  delegate: 'поручи другому устройству удалённая задача делегировать delegate remote',
  device_shell: 'команда shell терминал на другом устройстве консоль git command',
  device_files: 'файлы на другом устройстве прочитать записать папка files',
  device_open: 'открыть приложение на устройстве ссылка браузер open app',
  device_notify: 'уведомление на устройстве пуш напоминание notify',
  preview_close: 'закрыть панель превью убрать окно preview close',
  memory_forget: 'забыть удалить из памяти forget',
  device_screen: 'экран другого устройства скриншот screenshot',
  preview_show: 'показать страницу рядом с чатом превью панель preview',
  make_image: 'картинка рисунок нарисовать изображение фото draw image',
  speak: 'озвучить голос вслух сказать voice say',
  remember: 'запомнить запомни remember',
  memory_recall: 'вспомнить что помнишь память recall',
  conversation_open: 'открыть разговор в телеграме максе conversation open',
  devices: 'устройства список устройств devices',
}

/** The names whose definitions wait in the catalog instead of the request. */
export const DEFERRED = new Set([
  'goals_room_update',
  'feed_post',
  'library_add',
  'draw_new_look',
  'make_music',
  'delegate',
  'device_shell',
  'device_files',
  'device_open',
  'device_notify',
  'preview_close',
  'memory_forget',
])

/** Definitions kept for later: the tool's own name is the key. */
const catalog = new Map<string, ToolDefinition>()

function words(text: string): string[] {
  return (text || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .split(/[^0-9a-zа-я]+/)
    .filter((w) => w.length >= 3 && !['что', 'как', 'мне', 'меня', 'мой', 'моя', 'мои', 'это', 'для', 'the', 'and', 'you'].includes(w))
}

function stem(word: string): string {
  for (const ending of ['ыми', 'ими', 'ого', 'его', 'ами', 'ями', 'ов', 'ев', 'ам', 'ям', 'ах', 'ях', 'ом', 'ем', 'ой', 'ей', 'ую', 'ая', 'ое', 'ие', 'ые', 'ый', 'ий', 'а', 'я', 'ы', 'и', 'у', 'ю', 'е', 'о', 'ь', 'й', 'ing', 'ed', 'es', 's']) {
    if (word.length - ending.length >= 3 && word.endsWith(ending)) return stem(word.slice(0, -ending.length))
  }
  return word
}

function same(a: string, b: string): boolean {
  return a === b || (a.length >= 4 && b.length >= 4 && a.slice(0, 4) === b.slice(0, 4))
}

function score(query: string, definition: ToolDefinition): number {
  const asked = words(query).map(stem)
  if (asked.length === 0) return 0
  const alias = words(WORDS[definition.name] || '').map(stem)
  const title = words(definition.name.replace(/_/g, ' ')).map(stem)
  const about = words(definition.description).map(stem)
  const hits = (doc: string[]) => asked.filter((q) => doc.some((d) => same(q, d))).length
  return 3 * hits(alias) + 2 * hits(title) + hits(about)
}

/**
 * Register a tool now, or keep it for `tool_search` when its name is deferred.
 * Every tool file calls this instead of `ctx.tools.register` directly.
 */
export function registerTool(ctx: Context, definition: ToolDefinition): () => void {
  if (!DEFERRED.has(definition.name)) return ctx.tools.register(definition)
  catalog.set(definition.name, definition)
  return () => catalog.delete(definition.name)
}

export function apply(ctx: Context): void {
  ctx.tools.register(
    defineTool({
      name: 'tool_search',
      description:
        "Find a tool by what you want to do, in the person's words. Some tools are not listed in this request: ask here first, then call the tool by the name this returns, with the arguments its schema shows. Returns the name, one line and the full input schema of the best matches.",
      parameters: {
        query: { type: 'string', required: true, description: 'What the person wants done, in their own words' },
        limit: { type: 'integer', description: 'How many tools to show, three by default' },
      },
      output: {
        schema: {
          type: 'object',
          properties: { tools: { type: 'array', items: { type: 'string' } }, text: { type: 'string' } },
          additionalProperties: false,
        },
        render: (_args, value) => [{ type: 'text', text: String(value.text ?? '') }],
      },
      async execute(args) {
        const asked = String(args.query || '').trim()
        if (!asked) throw new Error('tool_search needs the query: what to do')
        const ranked = [...catalog.values()]
          .map((definition) => ({ definition, points: score(asked, definition) }))
          .filter((row) => row.points > 0)
          .sort((a, b) => b.points - a.points || a.definition.name.localeCompare(b.definition.name))
          .slice(0, Math.max(1, Math.min(Number(args.limit || 3), 5)))
        if (ranked.length === 0) {
          return {
            tools: [],
            text: `Nothing matched ${JSON.stringify(asked)}. The tools there are: ${[...catalog.keys()].sort().join(', ')}. If none does the job, say so instead of inventing one.`,
          }
        }
        const lines: string[] = []
        for (const { definition } of ranked) {
          // registering makes it callable (and visible) from the next step on
          ctx.tools.register(definition)
          catalog.delete(definition.name)
          lines.push(
            `- ${definition.name}: ${definition.description}\n  schema: ${JSON.stringify(definition.parameters)}`,
          )
        }
        return {
          tools: ranked.map((row) => row.definition.name),
          text: `${lines.join('\n')}\n\nCall the tool by name with arguments that fit its schema.`,
        }
      },
    }),
  )
}
