/** Account-scoped UI navigation, acknowledged only by the rendered conversation. */
import { randomUUID } from 'node:crypto'
export interface ConversationNavigationRequest { id: string; cid: string; sessionId: string; expires: number }
export class ConversationNavigation {
  request: ConversationNavigationRequest | null = null
  private pending: { scope: string; resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> } | undefined
  constructor(private readonly scope: () => string, private readonly changed: () => void, private readonly timeout = 15_000) {}
  open(cid: string, sessionId: string): Promise<Record<string, unknown>> {
    const scope = this.scope()
    if (!scope || !cid || !sessionId) return Promise.reject(new Error('Sign in and choose a shared conversation'))
    if (this.pending) return Promise.reject(new Error('Another conversation switch is in progress'))
    return new Promise((resolve, reject) => {
      this.request = { id: randomUUID(), cid, sessionId, expires: Date.now() + this.timeout }
      const timer = setTimeout(() => this.finish(new Error('The desktop did not confirm opening the conversation')), this.timeout)
      this.pending = { scope, resolve, reject, timer }
      this.changed()
    })
  }
  acknowledge(id: string, sessionId: string): boolean {
    const request = this.request, pending = this.pending
    if (!request || !pending || request.id !== id || request.sessionId !== sessionId) return false
    if (pending.scope !== this.scope() || Date.now() >= request.expires) { this.finish(new Error('The account changed or navigation expired')); return false }
    this.finish()
    return true
  }
  cancel(): void { if (this.pending) this.finish(new Error('Conversation navigation was cancelled')) }
  private finish(error?: Error): void {
    const pending = this.pending, request = this.request
    if (!pending || !request) return
    clearTimeout(pending.timer); this.pending = undefined; this.request = null; this.changed()
    if (error) pending.reject(error)
    else pending.resolve({ opened: true, channel: 'desktop', cid: request.cid, sessionId: request.sessionId, client_view_changed: true, created: false })
  }
}
