/** Remember a previous connection even while the socket is down. */
export function advanceConnection(previous: { connected: boolean; seen: boolean }, connected: boolean) {
  return {
    connected,
    seen: previous.seen || connected,
    recovered: connected && !previous.connected && previous.seen,
  };
}
