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
  const wrapped = []
  const budget = new TurnBudget({
    ...options,
    stop: (sessionId, turn) => stopped.push([sessionId, turn]),
    tell: (sessionId, turn, steps, minutes) => told.push([sessionId, turn, steps, minutes]),
    ...(options.wrapUp === false ? {} : { wrapUp: (sessionId, turn, count) => wrapped.push([sessionId, turn, count]) }),
  })
  return { budget, stopped, told, wrapped }
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

test('the default budget does not kill a slow turn by elapsed time', () => {
  const { budget, stopped } = make()
  budget.onEvent('s1', { type: 'turn/start', data: { turn: 1 } })
  budget.onEvent('s1', { type: 'step/start', data: { turn: 1, step: 1 } })
  const since = budget.view('s1').since
  budget.sweep(since + 90 * 60_000)
  assert.deepEqual(stopped, [])
  assert.equal(budget.view('s1').steps, 1)
})

test('at the cap the turn is asked to answer first, not cut off', () => {
  const { budget, stopped, told, wrapped } = make({ steps: 3 })
  steps(budget, 's1', 1, 3)
  // the step that reaches the cap asks for the answer: nothing is stopped, nobody is told yet
  assert.deepEqual(wrapped, [['s1', 1, 3]])
  assert.deepEqual(stopped, [])
  assert.deepEqual(told, [])
  // and it keeps a few steps to give that answer
  budget.onEvent('s1', { type: 'step/start', data: { turn: 1, step: 4 } })
  assert.deepEqual(stopped, [])
  assert.deepEqual(wrapped, [['s1', 1, 3]])
})

test('a turn that keeps calling tools past the grace is ended, with the numbers said', () => {
  const { budget, stopped, told, wrapped } = make({ steps: 3 })
  steps(budget, 's1', 1, 3 + 8)
  assert.equal(wrapped.length, 1)
  assert.deepEqual(stopped, [['s1', 1]])
  assert.equal(told.length, 1)
})

test('without a wrap-up the cap ends the turn at once, as before', () => {
  const { budget, stopped, told } = make({ steps: 3, wrapUp: false })
  steps(budget, 's1', 1, 6)
  assert.deepEqual(stopped, [['s1', 1]])
  assert.equal(told.length, 1)
  assert.equal(told[0][2], 3)
})

test('the same turn is never stopped twice', () => {
  const { budget, stopped } = make({ steps: 1, wrapUp: false })
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
  // the clock running out asks for the answer too, and the turn is ended only past the grace
  budget.sweep(since + 21 * 60_000)
  assert.deepEqual(stopped, [])
  budget.sweep(since + 23 * 60_000)
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
  const { budget, stopped } = make({ steps: 3, wrapUp: false })
  steps(budget, 'a', 1, 2)
  steps(budget, 'b', 1, 2)
  budget.onEvent('a', { type: 'step/start', data: { turn: 1, step: 3 } })
  assert.deepEqual(stopped, [['a', 1]])
  assert.equal(budget.view('b').steps, 2)
  assert.equal(budget.views().length, 2)
})
