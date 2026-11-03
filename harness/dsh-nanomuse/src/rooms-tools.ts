/**
 * The rooms, from inside a chat: the tools that let the agent write into the
 * person's Goals, Feed and Library (`goals_room_update`, `feed_post`, `library_add`),
 * and the prompt context that tells it what the goals are, so "how is my
 * flight-price goal doing?" needs no room open. A preset row, like Reach; it
 * needs the rooms service (`dsh-nanomuse/rooms`) and does nothing without it.
 */
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type {} from '@deepseek-ai/dsh-system-prompt'
import type {} from './cloud.ts'
import type {} from './rooms.ts'
import { AREAS, languageName } from './rooms.ts'

export const name = 'nanomuse-rooms-tools'
export const inject = ['tools', 'nanomuseRooms', 'nanomuseCloud']

export function apply(ctx: Context): void {
  const rooms = ctx.nanomuseRooms

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'goals_room_update',
          description: "Update one of the person's goals in their Goals room (NanoLika's room, not this session's own goal — that is update_goal): the one-line status, a timeline entry, the title, or mark it done/paused/tracking. Call it whenever something happens on a goal worth noting — a check done, a price seen, a step finished. The goals and their ids are in your context.",
          parameters: {
            goal_id: { type: 'string', required: true, description: 'The goal id from your context, or its exact title.' },
            summary: { type: 'string', description: "The goal's status in one line, as it should read under the title (e.g. 'Lowest fare today USD 879.90, still above the ¥4,000 line')." },
            activity_title: { type: 'string', description: 'A timeline entry, one line (what happened).' },
            activity_text: { type: 'string', description: 'The entry’s detail, one or two sentences.' },
            title: { type: 'string', description: 'A better title for the goal.' },
            status: { type: 'string', description: 'tracking, done or paused.' },
          },
          output: {
            schema: { type: 'object', properties: { goal_id: { type: 'string' }, title: { type: 'string' }, status: { type: 'string' } }, additionalProperties: false },
            render: (_args, value) => [{ type: 'text', text: `Goal "${value.title}" updated (${value.status}).` }],
          },
          async execute(args) {
            const goal = await rooms.updateGoal(args.goal_id, {
              ...(args.summary ? { summary: args.summary } : {}),
              ...(args.title ? { title: args.title } : {}),
              ...(args.status ? { status: args.status } : {}),
              ...(args.activity_title ? { activity: { title: args.activity_title, text: args.activity_text ?? '' } } : {}),
            })
            return { goal_id: goal.id, title: goal.title, status: goal.status }
          },
          presentCall: (args) => ({ card: 'generic', title: `Update goal ${args.goal_id || ''}`, kind: 'other', rawInput: args }),
        }),
      ),
    'nanomuse rooms: goals_room_update',
  )

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'feed_post',
          description: "Write a post into the person's Feed (their 动态 room): something they asked you to put there, a digest you made, a finding worth keeping. Markdown body, links inline. Not for ordinary replies.",
          parameters: {
            title: { type: 'string', required: true, description: 'At most 12 words.' },
            body: { type: 'string', required: true, description: '60-200 words of Markdown; no heading.' },
            area: { type: 'string', description: `One of ${AREAS.join(', ')}.` },
            emoji: { type: 'string', description: 'One emoji that fits.' },
            image: { type: 'string', description: 'A direct https image URL to show with it, if there is one.' },
          },
          output: {
            schema: { type: 'object', properties: { id: { type: 'string' }, title: { type: 'string' } }, additionalProperties: false },
            render: (_args, value) => [{ type: 'text', text: `Posted to the feed: ${value.title}` }],
          },
          async execute(args) {
            const post = await rooms.addPost({ title: args.title, body: args.body, ...(args.area ? { area: args.area } : {}), ...(args.emoji ? { emoji: args.emoji } : {}), ...(args.image ? { image: args.image } : {}) })
            return { id: post.id, title: post.title }
          },
          presentCall: (args) => ({ card: 'generic', title: `Post to the feed: ${args.title || ''}`, kind: 'other', rawInput: args }),
        }),
      ),
    'nanomuse rooms: feed_post',
  )

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'library_add',
          description: "Put a file into the person's Library (their 资源库 room): a document, a web page, an image, a video or audio you made or found for them. `present` does this too for files you deliver at the end of a task; use this for files you want listed without delivering them.",
          parameters: {
            path: { type: 'string', required: true, description: 'Absolute path, or relative to the working directory.' },
            description: { type: 'string', description: 'What it is, in a few words.' },
          },
          output: {
            schema: { type: 'object', properties: { id: { type: 'string' }, name: { type: 'string' }, kind: { type: 'string' } }, additionalProperties: false },
            render: (_args, value) => [{ type: 'text', text: `In the Library: ${value.name} (${value.kind}).` }],
          },
          async execute(args, exec) {
            const item = await rooms.addToLibrary(args.path, args.description ?? '', exec.agent?.session.id ?? '', exec.agent?.session.header.cwd)
            if (!item) throw new Error(`No file at ${args.path}.`)
            return { id: item.id, name: item.name, kind: item.kind }
          },
          presentCall: (args) => ({ card: 'generic', title: `Add to the Library: ${args.path || ''}`, kind: 'other', rawInput: args }),
        }),
      ),
    'nanomuse rooms: library_add',
  )

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'remember',
          description: "Keep one fact about the person for every later chat (their Memory): a preference, a name, a routine, a decision they made. One line, in their words' language. Use it when they tell you something they will expect you to know next time; not for the task at hand.",
          parameters: {
            text: { type: 'string', required: true, description: 'The fact, one line, at most 400 characters.' },
          },
          output: {
            schema: { type: 'object', properties: { id: { type: 'string' }, text: { type: 'string' } }, additionalProperties: false },
            render: (_args, value) => [{ type: 'text', text: `Remembered: ${value.text}` }],
          },
          async execute(args) {
            const item = await rooms.remember(args.text, 'agent')
            if (!item) throw new Error('Nothing to remember.')
            return { id: item.id, text: item.text }
          },
          presentCall: (args) => ({ card: 'generic', title: `Remember: ${(args.text || '').slice(0, 60)}`, kind: 'other', rawInput: args }),
        }),
      ),
    'nanomuse rooms: remember',
  )

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'draw_new_look',
          description: "Open the avatar studio on the person's screen with a description of the new look they asked for (\"change your avatar to an orange cat with a scarf\"). Four candidates are drawn there and they pick one; you do not draw the pictures yourself. Call it only when they ask for a new look.",
          parameters: {
            description: { type: 'string', required: true, description: 'The character, in a few words, in the language they used.' },
            style: { type: 'string', description: 'muse (default), flat, clay, watercolor, pixel, line or sticker — only when they named one.' },
          },
          output: {
            schema: { type: 'object', properties: { opened: { type: 'boolean' } }, additionalProperties: false },
            render: () => [{ type: 'text', text: 'The avatar studio is open on their screen; they pick one of four candidates there.' }],
          },
          async execute(args) {
            rooms.requestStudio(args.description, args.style ?? 'muse')
            return { opened: true }
          },
          presentCall: (args) => ({ card: 'generic', title: `New look: ${(args.description || '').slice(0, 60)}`, kind: 'other', rawInput: args }),
        }),
      ),
    'nanomuse rooms: draw_new_look',
  )

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'make_music',
          description:
            "Make one music track for the person and open it beside the chat. Pick the model from the account's music menu by what they asked for: music-suno-track — a full song with vocals or an instrumental (25 ₽), music-sounds-track — a short sound effect or sample (3.50 ₽), music-lyria-track — a short musical clip (7 ₽). Use sounds for a whoosh/rain/click, lyria for a sting, suno for a song. Pass style/title/instrumental when they named them; with custom=true the prompt is the lyrics and style and title are required. The charge is per track; say the model and the price in one line after it is ready. Do not invent another model id.",
          parameters: {
            prompt: { type: 'string', required: true, description: 'What to make: a description, or the lyrics when custom=true (at most 5000 characters).' },
            model: { type: 'string', description: 'music-suno-track (song, 25 ₽, default), music-sounds-track (effect, 3.50 ₽) or music-lyria-track (short clip, 7 ₽).' },
            title: { type: 'string', description: 'The track title, when they gave one (custom mode requires it).' },
            style: { type: 'string', description: 'Genre and mood, e.g. "lo-fi, calm, piano" (custom mode requires it).' },
            instrumental: { type: 'boolean', description: 'No vocals.' },
            custom: { type: 'boolean', description: 'Treat prompt as lyrics; requires style and title.' },
            duration: { type: 'number', description: 'Length in seconds, 10–360; only with custom=true.' },
          },
          output: {
            schema: {
              type: 'object',
              properties: { path: { type: 'string' }, model: { type: 'string' }, bytes: { type: 'number' }, title: { type: 'string' } },
              additionalProperties: false,
            },
            render: (args, value) => [{ type: 'text', text: `Track ready: ${value.title || args.prompt.slice(0, 60)} (${value.model}, ${Math.round((value.bytes ?? 0) / 1024)} KB).` }],
          },
          async execute(args, exec) {
            const prompt = String(args.prompt ?? '').trim()
            if (!prompt) throw new Error('What should the track be about?')
            const model = String(args.model || 'music-suno-track').trim()
            const allowed = new Set(['music-suno-track', 'music-sounds-track', 'music-lyria-track'])
            if (!allowed.has(model)) throw new Error(`Unknown music model ${model}; use ${[...allowed].join(', ')}`)
            if (args.custom && (!args.style || !args.title)) throw new Error('Custom mode needs both style and title')
            const audio = await ctx.nanomuseCloud.generateMusic(prompt, model, {
              ...(args.title ? { title: args.title } : {}),
              ...(args.style ? { style: args.style } : {}),
              ...(args.instrumental ? { instrumental: true } : {}),
              ...(args.custom ? { customMode: true } : {}),
              ...(args.duration ? { duration: args.duration } : {}),
            })
            const cwd = exec.agent?.session.header.cwd || process.cwd()
            const dir = join(cwd, 'music')
            await mkdir(dir, { recursive: true })
            const path = join(dir, `music-${Date.now()}.mp3`)
            await writeFile(path, audio)
            ctx.nanomuseCloud.showPreview({ path, title: args.title || 'Музыка' })
            return { path, model, bytes: audio.length, title: args.title || '' }
          },
          presentCall: (args) => ({ card: 'generic', title: `Music: ${(args.title || args.prompt || '').slice(0, 60)}`, kind: 'other', rawInput: args }),
        }),
      ),
    'nanomuse rooms: make_music',
  )

  ctx.effect(
    () =>
      ctx.tools.register(
        defineTool({
          name: 'make_image',
          description:
            "Make one picture for the person. It uses the image model chosen in Settings → Models (the account or an own key), not a provider API you call yourself. The result carries a stable link: put it in your reply as ![picture](link), and the chat shows the picture on this computer, on the other devices and in a messenger. Never say the picture is 'open beside the chat' — it belongs in the reply. Use it when they ask for a picture, illustration, logo concept, diagram-like visual or an edit-like redraw. Do not write a script or a provider client for this.",
          parameters: {
            prompt: { type: 'string', required: true, description: 'What to draw, in the person\'s words (at most 2000 characters).' },
            title: { type: 'string', description: 'What to call the picture in the pane.' },
          },
          output: {
            schema: { type: 'object', properties: { path: { type: 'string' }, bytes: { type: 'number' }, url: { type: 'string' } }, additionalProperties: false },
            render: (_args, value) => [{
              type: 'text',
              text: value.url
                ? `Picture ready (${Math.round((value.bytes ?? 0) / 1024)} KB). Link: ${value.url}. Put it in the reply as ![picture](${value.url}) — the chat shows it.`
                : `Picture ready (${Math.round((value.bytes ?? 0) / 1024)} KB), but it could not be uploaded for the chat.`,
            }],
          },
          async execute(args, exec) {
            const prompt = String(args.prompt ?? '').trim()
            if (!prompt) throw new Error('What should the picture show?')
            const png = await ctx.nanomuseCloud.studioDraw(prompt, false)
            const cwd = exec.agent?.session.header.cwd || process.cwd()
            const dir = join(cwd, 'images')
            await mkdir(dir, { recursive: true })
            const path = join(dir, `image-${Date.now()}.png`)
            await writeFile(path, png)
            // The picture goes into the account's storage and comes back as a link: the
            // chat draws it on this computer and on every other device, and the messenger
            // can send it as a photo. The side pane is not the place for the answer.
            const url = await ctx.nanomuseCloud.upload(`image-${Date.now()}.png`, png, 'image/png')
            return { path, bytes: png.length, url }
          },
          presentCall: (args) => ({ card: 'generic', title: `Picture: ${(args.title || args.prompt || '').slice(0, 60)}`, kind: 'other', rawInput: args }),
        }),
      ),
    'nanomuse rooms: make_image',
  )

  ctx.inject(['systemPrompt'], (ctx) => {
    // The map of what this computer and this account already have. The point is to stop
    // the model before it writes an MCP server, an OAuth flow or a provider client for
    // something the app already does. Keep the names exact: the model looks for tools by
    // name, and the person looks for settings by the labels below.
    ctx.effect(
      () =>
        ctx.systemPrompt.context({
          name: 'nanolika-capabilities',
          order: 54,
          text: () =>
            [
              '## What is already here — use it before writing code',
              'This computer and this account already carry the tools below. Before writing a script, an MCP server, a bridge, an OAuth flow, an IMAP client or a provider client, find the task here and use the named tool or settings page. If the task is not here, say what is missing and ask; never invent a model id, a key, an endpoint or an integration.',
              '',
              '- **Mail**: Settings → Connectors → Mailbox (Настройки → Коннекторы → Почта). When it is connected, the tools are `read_emails` and `send_email`: use them to search, read, draft and send. If they are not in your tool list, the connector is off: the same card shows the setup (`nanomuse vault set EMAIL_ADDRESS`, `nanomuse vault set EMAIL_PASSWORD`, then `[connectors.email]` in `~/.nanomuse/config.toml` with `imap_host`/`smtp_host`; for Yandex `imap.yandex.ru` / `smtp.yandex.ru` and an app password, then restart). Ask the person to connect it; never write an IMAP client, an OAuth flow or an MCP server for mail as a workaround. Sending is confirmed every time.',
              '- **Calendar and contacts**: the same Settings → Connectors page has built-in `calendar` and `contacts` tools. Use them; do not parse .ics/.vcf by hand or build a bridge.',
              '- **Other services** (Notion, GitHub, Linear, Slack, Google Drive and the rest of the catalogue): Settings → Connectors mounts them as `mcp__<server>__<tool>`. Use the mounted tools; do not write a client for a service the page already lists.',
              '- **Word / PowerPoint / Excel**: use the installed skills `docx`, `pptx`, `xlsx` through `tool-skill` (Settings → Skills / Настройки → Навыки). Do not pip install python-docx/openpyxl or write a parser unless the skill cannot do the job.',
              '- **Pictures**: `make_image` draws through the image model chosen in Settings → Models (Настройки → Модели) and returns a stable link. Always put that link in your reply as `![picture](link)`: the chat shows the picture here, on the other devices and in a messenger. Never say it is "open beside the chat". Avatar looks are `draw_new_look` (opens the studio with four candidates). Do not call a provider image API directly.',
              '- **Music**: `make_music` — `music-suno-track` (song/instrumental, 25 ₽), `music-sounds-track` (effect, 3.50 ₽), `music-lyria-track` (short clip, 7 ₽). Pick by the request, state the price, the tool opens the MP3 in the side pane.',
              '- **Speech out loud**: `speak` reads an answer with the system voice; it does not use the cloud voice model. Cloud speech is the app\'s voice feature, not a tool you call.',
              '- **Video**: the video model in Settings → Models is used by the avatar animation (Settings → Media / Настройки → Медиа). There is no general `make_video` tool in this build. Do not write a video client; if the person asks for a clip, say what is available.',
              '- **Files**: `tool-fs` (read/write/edit/list) and `fs_search`; use `bash` only for commands, not for reading or writing when the fs tools can do it.',
              '- **Web**: `web_search` and `web_fetch`; use them before writing a scraper.',
              '- **This screen**: `computer_screen` and `computer_act` (Settings → Computer use / Настройки → Управление компьютером). Do not use AppleScript, pyautogui or a custom clicker.',
              '- **Other devices**: `devices`, `device_screen`, `device_shell`, `device_files`, `device_open`, `device_notify`, `delegate` (the account\'s hub).',
              '- **The side pane**: `preview_show` / `preview_close`; a picture, a clip, a PDF or a page you made belongs here.',
              '- **Memory and rooms**: `remember`, `recall`, `forget`; `library_add`, `feed_post`, `goals_room_update`.',
              '- **Code and research**: `bash`, `fs`, `subagent`, plan mode. Do not build an integration that a named tool or settings page already covers.',
              '',
              'Before spending money on media, name the model and the price in one line. If the model is not open on the tariff, pick the nearest allowed model of that kind and say which one you used. When a tool refuses because a connection is missing, go to the settings page named above; do not code around it.',
            ].join('\n'),
        }),
      'nanomuse: capability map',
    )
    // The first conversation (C4): what the model is told for the chat it is bound to, by
    // phase — nothing anywhere else, nothing once it is over. The assembly names its agent.
    ctx.effect(
      () =>
        ctx.systemPrompt.context({
          name: 'nanolika-firstrun',
          order: 56,
          text: (context) => rooms.firstRunAddendum((context as { agent?: { session?: { id?: string } } }).agent?.session?.id),
        }),
      'nanomuse rooms: first conversation context',
    )
    ctx.effect(
      () =>
        ctx.systemPrompt.context({
          name: 'nanolika-rooms',
          order: 55,
          text: () => {
            const goals = rooms.goals
            const lines: string[] = []
            const persona = rooms.persona
            if (persona) lines.push('Who you are, as the person wrote it in your IDENTITY.md and SOUL.md (follow it):', persona, '')
            if (goals.length) {
              lines.push("The person's goals (their Goals room; update them with goals_room_update, or end a check with one fenced block tagged `nanomuse-goal-update` holding {\"goal_id\", \"progress\": 0-100, \"status\": \"on_track|attention|done\", \"note\"} — the app turns it into a card):")
              for (const goal of goals.slice(0, 12)) lines.push(`- ${goal.id} · ${goal.title} (${goal.category}, ${goal.status}${goal.progress >= 0 ? `, ${goal.progress}%` : ''})${goal.summary ? `: ${goal.summary}` : ''}`)
            } else {
              lines.push('The person has no goals in their Goals room yet; when they state a long-term aim, offer to track it there. They create one from the room ("Create goal › category"), or you can, when they agree: ask what, why and by when, how often to check, then write EXACTLY ONE fenced block tagged `nanomuse-goal` with {"title", "why", "category", "check_every_hours" (0 = daily), "check_time": "HH:MM", "steps": [...], "first_check"} — the app makes the goal, its chat and its checks from it, and shows it as a card. Do not describe the block.')
            }
            if (rooms.feedInstructions) lines.push(`What the person wants in their Feed: ${rooms.feedInstructions.slice(0, 300)}`)
            const memory = rooms.memory
            if (memory.length) {
              lines.push('', 'What you remember about the person (their Memory; add to it with remember):')
              for (const item of memory.slice(-60)) lines.push(`- ${item.text}`)
            } else {
              lines.push('', 'You remember nothing about the person yet; when they tell you something they will expect you to know next time, keep it with remember.')
            }
            // the words under the face: the person watches the step's own description there
            lines.push('', `Where a tool takes a \`description\` or \`step\` argument (bash, pwsh, run_code, the computer_* tools), write there what the step does for the person, in ${languageName(rooms.lang)}, in a few words ("打开携程网站", "Check the login page") — it is shown under your face while the tool runs. Fill it in on every such call.`)
            // the person's language governs the thinking too, not just the answer: the app shows
            // the step's own title and summary, and English there reads as a foreign voice
            lines.push('', `Think and write your intermediate reasoning — the title and the one-line summary of each step you take — in ${languageName(rooms.lang)}, the language the person reads. Keep code, commands, file paths, identifiers and quotes exactly as they are.`)
            return lines.join('\n')
          },
        }),
      'nanomuse rooms: prompt context',
    )
  })
}
