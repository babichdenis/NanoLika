/**
 * The side pane, from inside a chat: `preview_show` puts a page, a picture, a PDF
 * or a small piece of HTML beside the conversation (the app's split pane), and
 * `preview_close` takes it down. The pane keeps nothing of its own — a URL is
 * loaded by the browser's own frame, inline HTML lives in the message that asked
 * for it — so what is shown here is gone as soon as it is replaced or closed.
 *
 * A preset row, like Reach: it needs the cloud row (`nanomuse-cloud`) that holds
 * the pane's state and broadcasts it to the app.
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type {} from './cloud.ts'

export const name = 'nanomuse-preview-tools'
export const inject = ['tools', 'nanomuseCloud']

/** Longest inline markup the pane takes: a card, a table, a small page — not a site. */
const MAX_HTML = 200_000

export function apply(ctx: Context): void {
  const cloud = ctx.nanomuseCloud

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'preview_show',
          description:
            'Show something beside the conversation, in the app\'s side pane: a page by its URL (a site, a document), a picture, a PDF, or a small piece of HTML you write yourself (a table, a card, a comparison). Use it when the person would rather look than read, and when a page or a picture is the answer. Put one thing in a call; call it again to replace what is there. Inline HTML is for small markup only, no scripts that fetch the network.',
          parameters: {
            url: { type: 'string', description: 'The address to show: a page, an image file or a PDF.' },
            html: { type: 'string', description: 'Inline markup instead of a URL, when writing the thing itself is simpler than finding it.' },
            title: { type: 'string', description: 'What the pane calls it, in the person\'s language.' },
          },
          output: {
            schema: {
              type: 'object',
              properties: { shown: { type: 'string' }, kind: { type: 'string' } },
              additionalProperties: false,
            },
            render: (_args, value) => [{ type: 'text', text: `Shown beside the chat: ${value.shown}` }],
          },
          async execute(args) {
            const url = (args.url ?? '').trim()
            const html = (args.html ?? '').trim()
            if (!url && !html) throw new Error('give a url or html to show')
            if (html.length > MAX_HTML) throw new Error('that markup is too long for the pane; show a page by URL instead')
            const shownItem = cloud.showPreview({ ...(url ? { url } : {}), ...(html ? { html } : {}), ...(args.title ? { title: args.title } : {}) })
            return { shown: shownItem.title || url || 'inline markup', kind: shownItem.kind }
          },
          presentCall: (args) => ({ card: 'generic', title: `Show ${args.title || args.url || 'markup'}`, kind: 'other', rawInput: args }),
        }),
      ),
    'nanomuse preview: preview_show',
  )

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'preview_close',
          description: 'Take the side pane down when what is in it is no longer needed.',
          parameters: {},
          output: {
            schema: { type: 'object', properties: { closed: { type: 'boolean' } }, additionalProperties: false },
            render: () => [{ type: 'text', text: 'The side pane is closed.' }],
          },
          async execute() {
            cloud.hidePreview()
            return { closed: true }
          },
          presentCall: () => ({ card: 'generic', title: 'Close the side pane', kind: 'other', rawInput: {} }),
        }),
      ),
    'nanomuse preview: preview_close',
  )
}
