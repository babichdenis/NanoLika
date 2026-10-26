/** Deterministic, local receipts for unfinished turns. Never store tool arguments or output. */
export interface TurnOutcome {
  sessionId: string; turn: number; at: number; cause: 'budget' | 'repeated' | 'error' | 'interrupted' | 'incomplete'
  completed: number; failed: number; pending: boolean; lastOperation?: string
}
interface Run { lastOperation?: string; turn: number; completed: number; failed: number; pending: boolean; final: boolean; calls: Map<string, string>; lastFailure: string; repeats: number; cause?: TurnOutcome['cause'] }
export class TurnOutcomes {
  private runs = new Map<string, Run>()
  constructor(private readonly save: (outcome: TurnOutcome) => void, private readonly stop: (sessionId: string) => void) {}
  mark(sessionId: string, cause: TurnOutcome['cause']): void { const run = this.runs.get(sessionId); if (run) run.cause = cause }
  onEvent(sessionId: string, event: { type: string; data?: unknown }): void {
    const d = (event.data ?? {}) as Record<string, any>
    if (event.type === 'turn/start') {
      if (this.runs.get(sessionId)?.turn === d.turn) return
      this.runs.set(sessionId, { turn: Number(d.turn), completed: 0, failed: 0, pending: false, final: false, calls: new Map(), lastFailure: '', repeats: 0 })
      return
    }
    const run = this.runs.get(sessionId)
    if (!run || (typeof d.turn === 'number' && d.turn !== run.turn)) return
    if (event.type === 'assistant/message') {
      const content = d.message?.content
      if (Array.isArray(content)) run.final = content.some((part: any) => part.type === 'text' && String(part.text ?? '').trim()) && !content.some((part: any) => part.type === 'tool-call')
    }
    if (event.type === 'tool/call') {
      run.pending = true; run.final = false
      run.lastOperation = operationLabel(String(d.name ?? ''), d.arguments)
      // Only transient fingerprints: never persisted or sent to the browser.
      run.calls.set(String(d.callId), JSON.stringify([d.name, d.arguments]))
    }
    if (event.type === 'tool/result') {
      const id = String(d.message?.toolCallId ?? d.message?.source?.callId ?? '')
      const fingerprint = run.calls.get(id)
      if (!fingerprint) return // ignore replayed or unmatched tool results
      run.calls.delete(id); run.pending = run.calls.size > 0
      if (d.message?.isError === true) {
        run.failed++
        run.repeats = fingerprint && fingerprint === run.lastFailure ? run.repeats + 1 : 1
        run.lastFailure = fingerprint ?? ''
        if (run.repeats >= 2 && !run.cause) { run.cause = 'repeated'; this.stop(sessionId) }
      } else { run.completed++; run.lastFailure = ''; run.repeats = 0 }
    }
    if (event.type !== 'turn/end') return
    this.runs.delete(sessionId)
    const kind = d.reason?.kind
    const cause = run.cause ?? (kind === 'error' ? 'error' : kind !== 'completed' ? 'interrupted' : !run.final ? 'incomplete' : undefined)
    if (cause) this.save({ sessionId, turn: run.turn, at: Date.now(), cause, completed: run.completed, failed: run.failed, pending: run.pending, ...(run.lastOperation ? { lastOperation: run.lastOperation } : {}) })
  }
}
export function outcomeText(o: TurnOutcome): string {
  const reason = { budget: 'Достигнут лимит времени или шагов.', repeated: 'Одна и та же операция дважды завершилась ошибкой. Повторять её без изменения подхода не буду.', error: 'Работа прервалась из-за ошибки.', interrupted: 'Работа прервана.', incomplete: 'Ход закончился без итогового ответа.' }[o.cause]
  return `${reason}${o.lastOperation ? ` Последняя операция: ${o.lastOperation}.` : ''} Операций без ошибки инструмента: ${o.completed}; с ошибкой: ${o.failed}.${o.pending ? ' Результат последней операции не подтверждён.' : ''} Завершение всей задачи не подтверждено. Перед продолжением нужно проверить уже сделанные изменения и причину остановки.`
}

/** Classify intent, never claim success or retain command text, paths or credentials. */
function operationLabel(name: string, args: unknown): string {
  let command = ''
  try { const parsed = typeof args === 'string' ? JSON.parse(args) : args; command = String(parsed?.command ?? '') } catch { /* unstructured arguments */ }
  if (/\b(build\.mjs|tsc|npm run build|pnpm build)\b/.test(command)) return 'проверка сборки'
  if (/\b(pytest|vitest|node --test)\b/.test(command)) return 'запуск тестов'
  if (/edit|write|patch/i.test(name)) return 'изменение файлов'
  if (/read|search|find|list/i.test(name)) return 'чтение или поиск'
  if (/browser|web/i.test(name)) return 'работа с веб-страницей'
  if (/bash|shell|pwsh/i.test(name)) return 'команда на компьютере'
  return 'вызов инструмента'
}

export const RECOVERY_RULES = `After a failed tool attempt, briefly tell the user what failed based on observed evidence and what you will change before retrying. After two consecutive unsuccessful attempts without new evidence, stop and explain the obstacle and a concrete next step. Do not repeat identical failed commands. At the end of unfinished work, distinguish changes actually made from results not verified; do not leave a promise as the final answer. Do not claim a task passed merely because a tool returned without an error. Keep this explanation concise and human-readable; do not expose private chain-of-thought.`

/** Restore only our bounded public fields, never arbitrary stored payloads. */
export function restoreOutcomes(raw: unknown): TurnOutcome[] {
  if (!Array.isArray(raw)) return []
  return raw.filter((o) => o && typeof o.sessionId === 'string' && Number.isFinite(o.turn) && Number.isFinite(o.at) && ['budget','repeated','error','interrupted','incomplete'].includes(o.cause) && Number.isFinite(o.completed) && Number.isFinite(o.failed) && typeof o.pending === 'boolean').slice(-100).map((o) => ({
    sessionId: o.sessionId, turn: o.turn, at: o.at, cause: o.cause, completed: Math.max(0,o.completed), failed: Math.max(0,o.failed), pending: o.pending,
    ...(['проверка сборки','запуск тестов','изменение файлов','чтение или поиск','работа с веб-страницей','команда на компьютере','вызов инструмента'].includes(o.lastOperation) ? {lastOperation:o.lastOperation} : {}),
  }))
}
