// A turn's budget (src/turn-budget.ts): the harness has no step cap of its own, so the
// plugin counts the steps and the minutes of every open turn, and stops the one that goes
// over — with words for the person instead of an hour of silence.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { TurnBudget } from '../lib/turn-budget.js'

/** A budget that records what it stopped and told, and lets the test move the clock. */
function make(options = {}) {
  const stopped = []
  const told = []
  const budget = new TurnBudget({
    ...options,
    stop: (sessionId, turn) => stopped.push([sessionId, turn]),
    tell: (sessionId, turn, steps, minutes) => told.push([sessionId, turn, steps, minutes]),
  })
  return { budget, stopped, told }
}

/** n steps of one turn. */
function steps(budget, sessionId, turn, count) {
  budget.onEvent(sessionId, { type: 'turn/start', data: { turn } })
  for (let i = 0; i < count; i += 1) budget.onEvent(sessionId, { type: 'step/start', data: { turn, step: i + 1 } })
}

test('a short turn is left alone and its steps are visible', () => {
  const { budget, stopped, told } = make()
  steps(budget, 's1', 3, 5)
  const view = budget.view('s1')
  assert.equal(view.steps, 5)
  assert.equal(view.limit, 40)
  assert.equal(view.turn, 3)
  assert.deepEqual(stopped, [])
  assert.deepEqual(told, [])
})

test('a turn over its steps is stopped once, with the numbers said out loud', () => {
  const { budget, stopped, told } = make({ steps: 3 })
  steps(budget, 's1', 1, 6)
  assert.deepEqual(stopped, [['s1', 1]])
  assert.equal(told.length, 1)
  // it is the step that reaches the cap that ends the turn
  assert.equal(told[0][2], 3)
})

test('the same turn is never stopped twice', () => {
  const { budget, stopped } = make({ steps: 1 })
  steps(budget, 's1', 1, 5)
  budget.sweep()
  assert.equal(stopped.length, 1)
})

test('a turn whose single tool runs too long is stopped by the clock', () => {
  const { budget, stopped, told } = make({ minutes: 20 })
  budget.onEvent('s1', { type: 'turn/start', data: { turn: 1 } })
  budget.onEvent('s1', { type: 'step/start', data: { turn: 1, step: 1 } })
  const since = budget.view('s1').since
  budget.sweep(since + 19 * 60_000)
  assert.deepEqual(stopped, [])
  budget.sweep(since + 21 * 60_000)
  assert.deepEqual(stopped, [['s1', 1]])
  assert.equal(told.length, 1)
})

test('a finished turn forgets its clock, a new one starts with a full budget', () => {
  const { budget, stopped } = make({ steps: 3 })
  steps(budget, 's1', 1, 2)
  assert.deepEqual(stopped, [])
  budget.onEvent('s1', { type: 'turn/end', data: { turn: 1 } })
  assert.equal(budget.view('s1'), null)
  steps(budget, 's1', 2, 2)
  assert.deepEqual(stopped, [])
  assert.equal(budget.view('s1').turn, 2)
})

test('sessions keep their own clocks', () => {
  const { budget, stopped } = make({ steps: 3 })
  steps(budget, 'a', 1, 2)
  steps(budget, 'b', 1, 2)
  budget.onEvent('a', { type: 'step/start', data: { turn: 1, step: 3 } })
  assert.deepEqual(stopped, [['a', 1]])
  assert.equal(budget.view('b').steps, 2)
  assert.equal(budget.views().length, 2)
})
