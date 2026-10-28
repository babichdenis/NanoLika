/** A synced bubble belongs beside the outer turn, outside collapsed tool groups. */
export function remoteFlowItem(node: HTMLElement, content: HTMLElement): HTMLElement {
  const flow = content.querySelector<HTMLElement>('[data-chat-flow]')
  let item = node
  if (!flow || !flow.contains(node)) return item
  while (item.parentElement && item.parentElement !== flow) item = item.parentElement
  return item
}

/** The harness reuses its content element when switching sessions. */
export function bindRemoteSession(content: HTMLElement, sessionId: string): void {
  if (content.getAttribute('data-nm-remote-session') === sessionId) return
  for (const bubble of content.querySelectorAll('.nm-remote')) bubble.remove()
  for (const row of content.querySelectorAll<HTMLElement>('[data-nm-remote]')) {
    row.removeAttribute('data-nm-remote')
    row.style.display = ''
  }
  content.setAttribute('data-nm-remote-session', sessionId)
}
