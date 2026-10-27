/**
 * A turn's budget: forty steps. Long-running tool calls are not cut off by a wall clock.
 *
 * DeepSeek Harness' agent loop has no cap of its own — a turn runs until the
 * model stops calling tools. On a long task (a search that keeps coming up
 * empty, a build, a test run, an ssh to a far server) that becomes an hour of
 * the person's time and a turn that never says anything; the model's own
 * answers take seconds, the wall clock goes on the tools and the steps.
 *
 * Count tool steps to stop runaway loops. Do not interrupt a slow but still
 * working command just because it crossed an arbitrary time limit.
 */
export interface TurnBudgetOptions {
  /** Steps a single turn may take. */
  steps?: number
  /** Optional wall-clock limit in minutes; disabled by default. */
  minutes?: number
  /** End the turn that went over. */
  stop: (sessionId: string, turn: number) => void
  /** Tell the person, in the words they will read. */
  tell: (sessionId: string, turn: number, steps: number, minutes: number) => void
  /**
   * Asked once, when the cap is reached, before the turn is ended: hand the person the
   * answer now instead of cutting the work off. The turn keeps `grace` more steps to do it.
   */
  wrapUp?: (sessionId: string, turn: number, steps: number) => void
  /** Steps a wrapping-up turn may still take before it is ended. */
  grace?: number
}

interface OpenTurn {
  turn: number
  steps: number
  since: number
  stopped: boolean
  /** The wrap-up was already asked for, so the grace steps are being spent. */
  warned: boolean
}

/** The safety cap every turn gets unless the caller names another. */
export const TURN_STEPS = 40
/** Wall-clock cancellation is disabled unless a caller explicitly opts in. */
export const TURN_MINUTES = 0
/** Steps a turn may still take after it was asked to answer now. */
export const TURN_GRACE = 8

/** What the app shows for a running turn: "step 12 of 40". */
export interface TurnView {
  sessionId: string
  turn: number
  steps: number
  limit: number
  minutes: number
  since: number
}

export class TurnBudget {
  private readonly open = new Map<string, OpenTurn>()
  private readonly options: TurnBudgetOptions

  constructor(options: TurnBudgetOptions) {
    this.options = options
  }

  get limit(): number { return this.options.steps ?? TURN_STEPS }
  get minutes(): number { return this.options.minutes ?? TURN_MINUTES }

  /** One session's open turn, or null when nothing is running there. */
  view(sessionId: string): TurnView | null {
    const state = this.open.get(sessionId)
    if (!state) return null
    return { sessionId, turn: state.turn, steps: state.steps, limit: this.limit, minutes: this.minutes, since: state.since }
  }

  /** The running turns, newest first, for the live frame. */
  views(): TurnView[] {
    return [...this.open.keys()].map((id) => this.view(id)).filter((v): v is TurnView => v !== null)
  }

  /** One Session event: the turn's start and end, and every step inside it. */
  onEvent(sessionId: string, event: { type: string; data?: unknown }): void {
    const data = (event.data ?? {}) as { turn?: number; step?: number }
    if (event.type === 'turn/start') {
      const turn = Number(data.turn ?? 0)
      const running = this.open.get(sessionId)
      // a turn already open (a resumed session, a replay) keeps its clock: the steps count on
      if (running && running.turn === turn) return
      this.open.set(sessionId, { turn, steps: 0, since: Date.now(), stopped: false, warned: false })
      return
    }
    if (event.type === 'turn/end') {
      this.open.delete(sessionId)
      return
    }
    if (event.type !== 'step/start') return
    const turn = Number(data.turn ?? 0)
    let state = this.open.get(sessionId)
    if (!state || state.turn !== turn) {
      state = { turn, steps: 0, since: Date.now(), stopped: false, warned: false }
      this.open.set(sessionId, state)
    }
    state.steps += 1
    this.check(sessionId, state)
  }

  /**
   * Check an optional wall-clock deadline. The step limit is checked as events arrive.
   */
  sweep(now = Date.now()): void {
    for (const [sessionId, state] of this.open) this.check(sessionId, state, now)
  }

  /**
   * Over the cap: first ask for the answer (once), and keep the turn alive for `grace` more
   * steps so the model can give it; ended only if the tools go on past that. A caller
   * without `wrapUp` keeps the plain behaviour — the cap ends the turn at once.
   */
  private check(sessionId: string, state: OpenTurn, now = Date.now()): void {
    if (state.stopped) return
    const minutes = (now - state.since) / 60_000
    const over = state.steps >= this.limit || (this.minutes > 0 && minutes >= this.minutes)
    if (!over) return
    const grace = this.options.grace ?? TURN_GRACE
    if (!state.warned && this.options.wrapUp) {
      state.warned = true
      this.options.wrapUp(sessionId, state.turn, state.steps)
      return
    }
    // the grace: those steps, or two more minutes when the clock was what ran out
    const pastSteps = state.steps >= this.limit + grace
    const pastClock = this.minutes > 0 && minutes >= this.minutes + 2
    if (state.warned && !pastSteps && !pastClock) return
    state.stopped = true
    this.options.tell(sessionId, state.turn, state.steps, Math.round(minutes))
    this.options.stop(sessionId, state.turn)
  }
}
