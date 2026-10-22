/**
 * The account's facts from inside a chat: `remember`, `recall` and `forget` go over the hub
 * to the device that keeps the one memory — the NanoLika runtime on the account's always-on
 * computer (the cloud bot), found by what it answers to `info`. A fact said here is known on
 * the phone and through the bot, and the other way round; this computer keeps no copy.
 *
 * With no such device online the tools refuse: a fact written into a place only this machine
 * can see is worse than a refusal, because the other devices would silently not know it.
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type {} from './cloud.ts'

export const name = 'nanomuse-memory-tools'
export const inject = ['tools', 'nanomuseCloud']

interface MemoryLine {
  id: string
  content: string
  category?: string
}

export function apply(ctx: Context): void {
  const cloud = ctx.nanomuseCloud
  let hostId = ''

  /** The device that keeps the account's memory: the one whose `info` names `memory.add`. */
  const host = async (): Promise<string> => {
    const hub = cloud.hub
    if (hostId && hub.devices.some((d: { id: string; online?: boolean }) => d.id === hostId && d.online)) return hostId
    for (const device of hub.devices as Array<{ id: string; kind?: string; online?: boolean }>) {
      if (device.id === hub.deviceId || device.kind === 'phone' || device.online === false) continue
      try {
        const info = await hub.call(device.id, 'info', {}, { timeoutMs: 4000 })
        const actions = Array.isArray(info.actions) ? (info.actions as string[]) : []
        if (actions.includes('memory.add')) {
          hostId = device.id
          return hostId
        }
      } catch {
        // not a runtime that keeps the account's memory: look at the next one
      }
    }
    throw new Error('the account memory is kept by the NanoLika cloud device, and it is not online right now')
  }

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'memory_remember',
          description:
            'Save a durable fact about the person or their preferences in the account memory shared by all their devices (the phone and the bot see it too). Keep it short and factual. Never store passwords or payment details here.',
          parameters: {
            content: { type: 'string', required: true, description: 'The fact, one short sentence.' },
            category: { type: 'string', description: 'profile, preference, contact, project, routine or general.' },
          },
          output: {
            schema: {
              type: 'object',
              properties: { id: { type: 'string' }, content: { type: 'string' } },
              additionalProperties: false,
            },
            render: (_args, value) => {
              const v = value as { id?: string; content?: string }
              return [{ type: 'text', text: `Remembered ${v.id ?? ''}: ${v.content ?? ''}` }]
            },
          },
          async execute(args) {
            const to = await host()
            const r = await cloud.hub.call(to, 'memory.add', {
              content: String(args.content ?? ''),
              category: String(args.category ?? 'general'),
            })
            return { id: String(r.id ?? ''), content: String(r.content ?? '') }
          },
        }),
      ),
    'nanomuse memory: remember',
  )

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'memory_recall',
          description:
            'Search the account memory — the facts about the person shared by all their devices — for what is relevant to `query`.',
          parameters: {
            query: { type: 'string', required: true },
            limit: { type: 'integer' },
          },
          output: {
            schema: {
              type: 'object',
              properties: {
                items: {
                  type: 'array',
                  items: {
                    type: 'object',
                    properties: { id: { type: 'string' }, content: { type: 'string' }, category: { type: 'string' } },
                    additionalProperties: false,
                  },
                },
              },
              additionalProperties: false,
            },
            render: (_args, value) => {
              const items = ((value as { items?: MemoryLine[] }).items ?? []).filter((m) => m && m.content)
              const text = items.map((m) => `[${m.id}] ${m.content}`).join('\n')
              return [{ type: 'text', text: text || 'Nothing in the account memory about that.' }]
            },
          },
          async execute(args) {
            const to = await host()
            const r = await cloud.hub.call(to, 'memory.search', {
              query: String(args.query ?? ''),
              limit: Number(args.limit ?? 8),
            })
            const items = Array.isArray(r.items) ? (r.items as MemoryLine[]) : []
            return { items }
          },
        }),
      ),
    'nanomuse memory: recall',
  )

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'memory_forget',
          description:
            'Drop a fact from the account memory shared by all the person’s devices: by its id (from recall) or by a query that matches it.',
          parameters: {
            id: { type: 'string', description: 'Memory id, m_xxxxxxxx.' },
            query: { type: 'string', description: 'Text of the fact, when the id is not at hand.' },
          },
          output: {
            schema: { type: 'object', properties: { forgotten: { type: 'integer' } }, additionalProperties: false },
            render: (_args, value) => {
              const n = Number((value as { forgotten?: number }).forgotten ?? 0)
              return [{ type: 'text', text: `Forgotten: ${n} line(s).` }]
            },
          },
          async execute(args) {
            const to = await host()
            const r = await cloud.hub.call(
              to,
              'memory.forget',
              args.id ? { id: String(args.id) } : { query: String(args.query ?? '') },
            )
            return { forgotten: Number(r.forgotten ?? 0) }
          },
        }),
      ),
    'nanomuse memory: forget',
  )
}
