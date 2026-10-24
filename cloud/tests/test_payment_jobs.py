import json
import time

import httpx
from test_payments import setup

from nanomuse_cloud.payment_jobs import reconcile_batch
from nanomuse_cloud.payments import OrderRequest, token


async def test_background_repairs_lost_notification_once(monkeypatch):
    _, client, user, p, _, bank = await setup(monkeypatch)
    caller = p.cloud.authenticate(user["api_key"])
    order = await p.create(caller, OrderRequest(amount="200", accept_terms=True, idempotency_key="job_fixture_order"))
    pid = p.order(order["order_id"])["payment_id"]
    bank[pid]["Status"] = "CONFIRMED"
    result = await reconcile_batch(p)
    assert result == {"checked": 1, "failed": 0, "recovered": 0}
    assert p.wallet(caller)["balance_kopecks"] == 20000
    assert (await reconcile_batch(p))["checked"] == 0
    await reconcile_batch(p, at=int(time.time()) + 61)
    assert p.wallet(caller)["balance_kopecks"] == 20000
    await client.aclose()


async def test_lost_init_payment_id_recovered_without_new_charge(monkeypatch):
    _, client, user, p, _, _ = await setup(monkeypatch)
    account = user["account"]["id"]
    with p.db.tx() as c:
        c.execute(
            "INSERT INTO tbank_orders(id,account_id,idempotency,plan,amount,terminal,created_at) VALUES('lost',?,'lost_init_fixture','',20000,?,?)",
            (account, p.terminal, int(time.time())),
        )
    calls = []

    def bank(request):
        body = json.loads(request.content)
        assert body["Token"] == token(body, p.password)
        calls.append(request.url.path)
        if request.url.path.endswith("CheckOrder"):
            return httpx.Response(
                200,
                json={
                    "Success": True,
                    "TerminalKey": p.terminal,
                    "OrderId": "lost",
                    "Payments": [{"PaymentId": "5678", "Amount": 20000, "Status": "CONFIRMED"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "Success": True,
                "TerminalKey": p.terminal,
                "OrderId": "lost",
                "PaymentId": "5678",
                "Amount": 20000,
                "Status": "CONFIRMED",
            },
        )

    p.transport = httpx.MockTransport(bank)
    result = await reconcile_batch(p)
    assert result == {"checked": 1, "failed": 0, "recovered": 1}
    assert p.order("lost")["payment_id"] == "5678"
    assert p.wallet(p.cloud.authenticate(user["api_key"]))["balance_kopecks"] == 20000
    assert len(calls) == 2 and all(not x.endswith("Init") for x in calls)
    await client.aclose()
