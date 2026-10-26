/** Keep an unfinished-turn receipt in the conversation flow, not pinned above the composer. */
import { outcomeText } from '../turn-outcomes.ts'
import { peekLive, subscribeLive } from './live.ts'

function flowItem(node: HTMLElement, content: HTMLElement): HTMLElement {
  let item = node
  while (
    item.parentElement &&
    item.parentElement !== content &&
    !item.parentElement.hasAttribute('data-chat-flow') &&
    item.parentElement.hasAttribute('data-chat-group-key')
  ) item = item.parentElement
  return item
}

/** Render the latest receipt as a compact, scrollable row in the active transcript. */
export function renderTurnOutcome(): () => void {
  let scheduled: ReturnType<typeof setTimeout> | undefined
  let stopped = false
  const schedule = () => {
    if (stopped || scheduled) return
    scheduled = setTimeout(() => {
      scheduled = undefined
      sweep()
    }, 60)
  }
  const sweep = () => {
    const active = new Set<HTMLElement>()
    const outcomes = peekLive().turnOutcomes ?? []
    for (const content of document.querySelectorAll<HTMLElement>('[data-conversation-content][data-conversation-session]')) {
      const sessionId = content.getAttribute('data-conversation-session') ?? ''
      const outcome = outcomes.filter((row) => row.sessionId === sessionId).at(-1)
      const old = content.querySelector<HTMLElement>('[data-nm-turn-outcome]')
      if (!outcome) {
        old?.remove()
        continue
      }
      active.add(content)
      const key = `${outcome.sessionId}:${outcome.turn}`
      let card = old
      if (!card || card.getAttribute('data-nm-turn-outcome') !== key) {
        card?.remove()
        card = document.createElement('details')
        card.setAttribute('data-nm-turn-outcome', key)
        card.style.cssText = 'margin:8px 12px 12px;padding:9px 13px;border-radius:15px 18px 14px 19px;background:var(--background-secondary,#f4f2e9);color:var(--text-primary,#37433c);font-size:13px;max-width:760px'
        const summary = document.createElement('summary')
        summary.style.cssText = 'cursor:pointer;line-height:1.45'
        summary.textContent = `\u0420\u0430\u0431\u043e\u0442\u0430 \u043d\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430 \u00b7 ${new Date(outcome.at).toLocaleString()} \u00b7 \u0445\u043e\u0434 ${outcome.turn}`
        const detail = document.createElement('p')
        detail.style.cssText = 'margin:8px 0 0;line-height:1.5'
        detail.textContent = outcomeText(outcome)
        card.append(summary, detail)
      }
      const rows = [...content.querySelectorAll<HTMLElement>('[data-chat-node-key]')]
      const last = rows.at(-1)
      if (last) {
        const anchor = flowItem(last, content)
        if (anchor.nextElementSibling !== card) anchor.insertAdjacentElement('afterend', card)
      } else {
        const seat = content.querySelector<HTMLElement>('[data-conversation-scroll] > [data-composer-seat]')
        if (seat && seat.previousElementSibling !== card) seat.insertAdjacentElement('beforebegin', card)
        else if (!seat && card.parentElement !== content) content.append(card)
      }
    }
    for (const card of document.querySelectorAll<HTMLElement>('[data-nm-turn-outcome]')) {
      const owner = card.closest<HTMLElement>('[data-conversation-content][data-conversation-session]')
      if (!owner || !active.has(owner)) card.remove()
    }
  }
  const observer = new MutationObserver(schedule)
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-conversation-session'] })
  const off = subscribeLive(schedule)
  schedule()
  return () => {
    stopped = true
    off()
    observer.disconnect()
    if (scheduled) clearTimeout(scheduled)
    document.querySelectorAll('[data-nm-turn-outcome]').forEach((card) => card.remove())
  }
}
