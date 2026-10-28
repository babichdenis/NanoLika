import assert from 'node:assert/strict'
import { test } from 'node:test'
import { bindRemoteSession, remoteFlowItem } from '../lib/client/remote-layout.js'

const element = (parentElement = null) => ({ parentElement })

test('a remote reply stays outside nested and collapsed process flows', () => {
  const outer = element()
  const group = element(outer)
  const collapsedBody = element(group)
  const innerFlow = element(collapsedBody)
  const hiddenContext = element(innerFlow)
  const content = { querySelector: () => outer }
  outer.contains = () => true
  assert.equal(remoteFlowItem(hiddenContext, content), group)
  const ordinary = element(outer)
  assert.equal(remoteFlowItem(ordinary, content), ordinary)
})

test('a chat without a flow retains its own anchor', () => {
  const node = element()
  assert.equal(remoteFlowItem(node, { querySelector: () => null }), node)
})

test('switching to a chat with no remote messages removes the previous chat bubbles', () => {
  const attributes = new Map([['data-nm-remote-session', 'old-chat']])
  const stale = { removed: false, remove() { this.removed = true } }
  const row = { style: { display: 'none' }, removeAttribute() { this.restored = true } }
  const content = {
    getAttribute: name => attributes.get(name),
    setAttribute: (name, value) => attributes.set(name, value),
    querySelectorAll: selector => selector === '.nm-remote' ? [stale] : [row],
  }
  bindRemoteSession(content, 'new-chat')
  assert.equal(stale.removed, true)
  assert.equal(row.style.display, '')
  assert.equal(attributes.get('data-nm-remote-session'), 'new-chat')
})

test('refreshing the same chat keeps its visible bubbles while history is fetched', () => {
  const content = { getAttribute: () => 'same-chat', querySelectorAll() { assert.fail('should retain current bubbles') } }
  bindRemoteSession(content, 'same-chat')
})
