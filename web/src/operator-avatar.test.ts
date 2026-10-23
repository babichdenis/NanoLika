import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { expect, test, vi } from 'vitest';
import { useOperatorAvatar } from './operator-avatar';

vi.mock('./api', () => ({ api: { cloud: async () => ({ base_url: 'https://avatar-test.invalid' }) } }));

test('only default faces subscribe, publication follows the manifest, reset returns to bundled art', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('document', { visibilityState: 'visible' });
  const id = 'a'.repeat(24);
  let active: string | null = id;
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ revision: 1, active, stills: active ? { idle: `/v1/brand/avatar/assets/${id}/idle.webp` } : {}, clips: {} }) }));
  vi.stubGlobal('fetch', fetcher);
  let result!: ReturnType<typeof useOperatorAvatar>;
  function Probe({ enabled }: { enabled: boolean }) { result = useOperatorAvatar(enabled); return null; }
  let root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(createElement(Probe, { enabled: false })); });
    expect(fetcher).not.toHaveBeenCalled();
    expect(result).toBeNull();
    await act(async () => { root.update(createElement(Probe, { enabled: true })); });
    expect(result?.stills.idle).toBe(`https://avatar-test.invalid/v1/brand/avatar/assets/${id}/idle.webp`);
    active = null;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(result).toBeNull();
    await act(async () => { root.update(createElement(Probe, { enabled: false })); });
    fetcher.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(fetcher).not.toHaveBeenCalled();
  } finally {
    await act(async () => { root.unmount(); });
    vi.useRealTimers();
    vi.unstubAllGlobals();
  }
});
