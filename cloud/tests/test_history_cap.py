from __future__ import annotations

import uuid

import pytest
from nanomuse_cloud.service import CloudError
from nanomuse_cloud.sync import LIMITS
from test_accounts import auth, make
from test_cloud import sign_up
from test_sync import conv, msg


def uid():
    return str(uuid.uuid4())


@pytest.mark.asyncio
async def test_history_cap_preserves_pages_rejects_new_and_accepts_after_deletion(monkeypatch):
    app, client, sender, _, cloud, _ = make()
    a = await sign_up(client, sender, 'history-a@example.test', 'a')
    b = await sign_up(client, sender, 'history-b@example.test', 'b')
    ka, kb = a['api_key'], b['api_key']
    for key in (ka, kb):
        assert (await client.put('/v1/sync/state', headers=auth(key), json={'enabled': True})).status_code == 200
    monkeypatch.setitem(LIMITS, 'messages', 3)
    cid, cid2 = uid(), uid()
    a_mids = [uid() for _ in range(4)]
    batch = {'device': 'a', 'conversations': [conv(cid), conv(cid2)], 'messages': [msg(a_mids[i], cid, text=f'saved-{i}') for i in range(4)]}
    failed = await client.post('/v1/sync/changes', headers=auth(ka), json=batch)
    assert failed.status_code == 409 and failed.json()['error']['code'] == 'message_limit'
    assert 'не синхронизировано' in failed.json()['error']['message']
    state = (await client.get('/v1/sync/state', headers=auth(ka))).json()
    assert state['counts']['conversations'] == state['counts']['messages'] == 0
    batch['messages'] = batch['messages'][:3]
    first = await client.post('/v1/sync/changes', headers=auth(ka), json=batch)
    assert first.status_code == 200 and first.json()['accepted'] == 5
    cursor, collected = 0, []
    while True:
        page = (await client.get(f'/v1/sync/changes?since={cursor}&limit=2', headers=auth(ka))).json()
        collected.extend(row for row in page['messages'] if not row['deleted'])
        cursor = page['cursor']
        if not page['more']:
            break
    assert {m['mid'] for m in collected} == set(a_mids[:3])
    assert {m['text'] for m in collected} == {'saved-0', 'saved-1', 'saved-2'}
    # Another account has its own independent capacity.
    other = await client.post('/v1/sync/changes', headers=auth(kb), json={'device': 'b', 'conversations': [conv(cid)], 'messages': [msg(uid(), cid)]})
    assert other.json()['accepted'] == 2
    # A retry cannot delete history; an explicit deletion frees one place.
    retry = await client.post('/v1/sync/changes', headers=auth(ka), json={'device': 'a', 'messages': [msg(a_mids[3], cid2)]})
    assert retry.status_code == 409 and retry.json()['error']['code'] == 'message_limit'
    freed = await client.post('/v1/sync/changes', headers=auth(ka), json={'device': 'a', 'messages': [msg(a_mids[0], cid, deleted=True), msg(a_mids[3], cid2)]})
    assert freed.json()['accepted'] == 2 and freed.json()['rejected'] == []
    data = (await client.get('/v1/sync/changes?since=0', headers=auth(ka))).json()['messages']
    assert {m['mid'] for m in data if not m['deleted']} == set(a_mids[1:])
    await client.aclose()


@pytest.mark.asyncio
async def test_already_above_limit_and_legacy_duplicate_do_not_trim(monkeypatch):
    app, client, sender, _, cloud, _ = make()
    a = await sign_up(client, sender, 'over-limit@example.test', 'a')
    key, account = a['api_key'], a['account']['id']
    await client.put('/v1/sync/state', headers=auth(key), json={'enabled': True})
    store, cid = app.state.sync, uid()
    monkeypatch.setitem(LIMITS, 'messages', 10)
    rows = [msg(uid(), cid, text=str(i)) for i in range(5)]
    assert store.push(account, 'a', [conv(cid)], rows)['accepted'] == 6
    monkeypatch.setitem(LIMITS, 'messages', 3)
    with pytest.raises(CloudError) as exc:
        store.push(account, 'a', [], [rows[0], msg(uid(), cid)])
    assert exc.value.code == 'message_limit'
    assert {r['text'] for r in store.changes(account, 0, 100)['messages']} == {str(i) for i in range(5)}
    await client.aclose()
