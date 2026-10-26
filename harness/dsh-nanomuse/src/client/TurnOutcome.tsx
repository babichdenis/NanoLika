/** A durable local receipt above the composer, scoped to the conversation being read. */
import { createElement as h } from 'react'
import { outcomeText } from '../turn-outcomes.ts'
import { useLive } from './live.ts'
import { useWin } from './win.ts'
export function TurnOutcomeCard() {
  const live = useLive()
  const current = useWin().current
  const outcome = live?.turnOutcomes?.filter((row) => row.sessionId === current).at(-1)
  if (!outcome) return null
  return h('details', { open: true, style: { margin: '8px 12px', padding: '10px 14px', borderRadius: '15px 18px 14px 19px', background: 'var(--background-secondary, #f4f2e9)', color: 'var(--text-primary, #37433c)', fontSize: 13, maxWidth: 760 }, key: `${outcome.sessionId}:${outcome.turn}` },
    h('summary', { style: { cursor: 'pointer' } }, `Итог незавершённой работы · ${new Date(outcome.at).toLocaleString()} · ход ${outcome.turn}`),
    h('p', { style: { margin: '8px 0 0', lineHeight: 1.5 } }, outcomeText(outcome)))
}
