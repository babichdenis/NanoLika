/**
 * A turn's budget: forty steps and twenty minutes.
 *
 * DeepSeek Harness' agent loop has no cap of its own — a turn runs until the
 * model stops calling tools. On a long task (a search that keeps coming up
 * empty, a build, a test run, an ssh to a far server) that becomes an hour of
 * the person's time and a turn that never says anything; the model's own
 * answers take seconds, the wall clock goes on the tools and the steps.
 *
 * So every open turn is counted here: its steps, and the minutes since it
 * started. The one that goes over is ended through the Session controller, and
 * the person is told where it stopped in their own words — the words come back
 * to them instead of the loop going on. A turn the person cancels themselves
 * ends the same way, and the counter forgets it.
 */
export interface TurnBudgetOptions {
  /** Steps a single turn may take. */
  steps?: number
  /** Minutes a single turn may run. */
  minutes?: number
  /** End the turn that went over. */
  stop: (sessionId: string, turn: number) => void
  /** Tell the person, in the words they will read. */
  tell: (sessionId: string, turn: number, steps: number, minutes: number) => void
}

interface OpenTurn {
  turn: number
  steps: number
  since: number
  stopped: boolean
}

/** The budget every turn gets unless the caller names another. */
export const TURN_STEPS = 40
export const TURN_MINUTES = 20

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
      this.open.set(sessionId, { turn, steps: 0, since: Date.now(), stopped: false })
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
      state = { turn, steps: 0, since: Date.now(), stopped: false }
      this.open.set(sessionId, state)
    }
    state.steps += 1
    this.check(sessionId, state)
  }

  /**
   * The clock alone: a turn whose single tool call runs for half an hour takes no
   * new steps, and is exactly the turn that must end. Called on a timer.
   */
  sweep(now = Date.now()): void {
    for (const [sessionId, state] of this.open) this.check(sessionId, state, now)
  }

  private check(sessionId: string, state: OpenTurn, now = Date.now()): void {
    if (state.stopped) return
    const minutes = (now - state.since) / 60_000
    if (state.steps < this.limit && minutes < this.minutes) return
    state.stopped = true
    this.options.tell(sessionId, state.turn, state.steps, Math.round(minutes))
    this.options.stop(sessionId, state.turn)
  }
}
