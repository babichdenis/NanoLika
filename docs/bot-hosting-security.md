# Bot hosting security contract

**Коротко по-русски.** Бот клиента живёт на его компьютере, мы на сервере не храним ни файлы, ни
переписку: в базе только хеши токенов, идентификаторы, тариф и счётчики. Наша роль — шлюз: пропускаем
вызовы Telegram через Ригу, пока тариф оплачен, и не пропускаем, когда нет. Документ фиксирует, что
защищаем, где единственная точка контроля, что обязан делать клиентский рантайм и как проверить каждое
утверждение командой. Главная находка: шлюз зовёт `api.telegram.org` напрямую, а этот адрес с сервера в
России недоступен, нужен явный транспорт через туннель к Риге, иначе шлюз не работает вообще.

Status: draft, 2026-10-23. Sources: `cloud/nanomuse_cloud/bot_gateway.py`, `cloud/nanomuse_cloud/db.py`,
measured behaviour of the production host.

---

## 1. Where everything lives

Decision of 2026-10-23: the bot runs on the client's own computer; the server keeps no bot content.

| Where | What runs there | What it holds |
|-------|-----------------|---------------|
| Client machine | nanoMuse app, the bot process, the agent's tools, the workspace | the Telegram bot token, the bot's files, its settings, the conversation history |
| Our relay | FastAPI + SQLite: accounts, plans, ledger, hub, `BotGateway` | `accounts`, `bot_projects` (platform, provider id, label, **`token_hash`**, `secret_hash`, `enabled`, created at), ledger rows |
| Our tunnel | `nanolika-tunnel:1.0.0` sidecar | nothing; it is a pipe to the Riga proxy |
| Transit only | relay memory | running Telegram calls, and incoming webhook deliveries on their way to the worker; never written to the database |

What the relay never stores: message text, files, attachments, the plaintext bot token, model keys of the
tenant, the tenant's workspace. Verified: the only writes in `bot_gateway.py` are `add_bot_project`,
`set_bot_project_enabled`, `delete_bot_project`; the webhook handler forwards and returns without a DB write;
`BotCredentialFilter` masks `api.telegram.org/bot<token>/` in logs.

One honest caveat: deliveries **pass through** the relay in memory. Transient processing is still processing
under 152-ФЗ, so the privacy page must say so plainly, and logs must never carry the body.

## 2. Threat model

| # | Threat | Who | Where it is stopped |
|---|--------|-----|---------------------|
| T1 | Prompt injection: the bot obeys a hostile instruction on the client's machine | whoever writes to the bot | the client runtime's Sentinel and sandbox (our app's problem, §4) |
| T2 | Free riding on our egress: use the Telegram path without paying | client | `require_plan` in the relay; no long-lived egress credential anywhere |
| T3 | A tenant reaching our host, other tenants, or our keys through the gateway | client | the gateway only forwards Telegram methods it knows, to a token it hashed and matched; no filesystem, no shell, no arbitrary URL |
| T4 | Our server holding what it promised not to hold | us | the DB schema in §1, redacted logs, no body persistence |

Note what is **not** a threat: "the client granted Lika permissions". Permissions come from the runtime's
config, never from a chat message. On the client's own machine the user is allowed to grant themselves
anything; our exposure starts where their traffic reaches us.

Also be honest about the limits: a client who does not want to pay can run their own proxy and use their own
bot token without us at all. So the gateway is a **service** gate, not an anti-abuse wall. What we actually
protect is our egress capacity and the model spend that runs through our keys. That is why the product's real
lever is model access, not the Telegram pipe.

## 3. Invariants

Our side (checked on the production host after every deploy):

| # | Invariant | Check |
|---|-----------|-------|
| S1 | The tunnel is reachable only from our own containers | from outside `nc -z <host> 8888` fails; nothing published in compose |
| S2 | Egress to Telegram goes through the tunnel, never direct | relay calls fail without the proxy transport, see §5 |
| S3 | A plan-less account can create, call, receive and resume nothing | `POST /v1/bot-projects/x/call` → 403 `bot_plan_required` |
| S4 | The bot token exists on our side only as a hash | `SELECT token_hash FROM bot_projects` is 64 hex chars; no plaintext column |
| S5 | Message bodies are never written to the database or logs | grep the code for `INSERT` in `bot_gateway.py`; log lines show `bot[redacted]` |
| S6 | Money moves only in the ledger, before the call | ledger row for the request; zero balance → 402 |
| S7 | Server-side secrets never reach a tenant | no tenant-supplied URL is ever dialled; the proxy address comes from our config |

Client side (requirements for the app that hosts the bot, i.e. our desktop and mobile apps):

| # | Invariant | Check |
|---|-----------|-------|
| C1 | The agent acts through the Sentinel with an honest `risk`, and dangerous actions ask the owner | a shell command shows a permission card |
| C2 | The bot token lives in the local vault or config with mode 600, never in logs or in a cloud copy | `grep -r <token> ~/.nanomuse` finds only the vault |
| C3 | The bot's workspace is a normal user directory; no elevation, no root | the process runs as the logged-in user |
| C4 | Every call to our gateway carries the account's own credential; a stolen project id alone buys nothing | `/call` without the matching token → 403 `bot_token_mismatch` |

## 4. What runs where: the split

- **The bot's brain, tools and history live on the client's machine.** Our app already enforces the Sentinel
  and the sandbox mode (`auto`, `bwrap`, container). The rules for it are C1 to C4 above; the container
  hardening in this document's earlier drafts is not needed on our host, because no tenant bot runs on it.
- **Our host runs the relay, the tunnel and the model gateway.** Its surface is HTTP/443, the relay's
  authenticated API, and outbound traffic to the Riga proxy. Nothing tenant-writable sits on it.
- **A paid plan gates the Telegram path.** Unpaid: the gateway returns 403, the webhook worker is closed, no
  deliveries are queued for replay. The client keeps its files and can host the bot wherever it likes.

## 5. The one gap that blocks the gateway today

`Worker` calls Telegram like this (approx. lines 131 to 140):

```python
url = f"https://api.telegram.org/bot{token}/{method}"
client = httpx.AsyncClient(transport=self.transport, verify=self.context(), trust_env=False, ...)
```

`trust_env=False` means no proxy from the environment, and `self.transport` is `None` in production, so the
relay dials `api.telegram.org` **directly**. Measured on the production host on 2026-10-23:

```
direct from host            → no response (blocked)
direct from a container     → URLError: [Errno 101] Network is unreachable
through tunnel:8888         → HTTP 200
```

Required change, small and explicit:

```python
# cloud/nanomuse_cloud/bot_gateway.py
import httpx
EGRESS = os.environ.get("BOT_EGRESS_PROXY", "")   # http://tunnel:8888 on the production host

def egress_transport() -> httpx.AsyncBaseTransport | None:
    return httpx.AsyncHTTPTransport(proxy=EGRESS) if EGRESS else None
```

Pass it where `BotGateway` is installed. Keep `trust_env=False`: the environment is not a trusted source. If
`EGRESS` is empty the gateway should refuse to start with a clear message, rather than fail per request.

## 6. The tunnel

- Image `nanolika-tunnel:1.0.0`, built from `/opt/nanolika-tunnel/Dockerfile` on the production host.
- `autossh -N -L 0.0.0.0:8888:127.0.0.1:8888 root@<riga>`; the far side is the tinyproxy on Riga.
- Private docker network only; nothing published; no capabilities; `no-new-privileges`.
- Consumers reach it as `http://tunnel:8888` (`CONNECT` for HTTPS). Tenants never touch it directly: only the
  relay does, so one shared sidecar is enough and per-tenant tunnels are not needed.
- Health: `nc -z 127.0.0.1 8888` inside the sidecar. If it dies, the Telegram path degrades for everyone and
  nothing else breaks.
- The Riga side is a dependency we must keep: tinyproxy on `46.8.228.71` plus the SSH key. Rotation and a
  second exit are follow-ups.

## 7. Money

- The ledger lives in the relay database (micro-yuan, `cost_uy` / `sale_uy`). Never in the client.
- Charge before the call; write the ledger row in the same transaction as the id.
- Every mutating call carries an idempotency key; a retry must not double-charge.
- Payments credit once: store the provider's payment id and refuse a second credit for the same id.
- The plan decides the Telegram path (`require_plan`) **and** the model budget. Since the bot's brain runs on
  the client, the only thing that keeps model spend on our meter is that the client's model calls go through
  `/v1/chat/completions`. A tenant with their own DeepSeek key does not need us; that is a product decision,
  not a bug.

## 8. Verification checklist

```bash
# plan gate, unknown project, no auth
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$CLOUD/v1/bot-projects/x/call" -d '{}'   # 401 or 403
# tunnel is private
nc -z <host> 8888 || echo "8888 закрыт снаружи"                                             # закрыт
# egress works and direct does not
docker exec cloud-relay-1 python -c "import httpx;print(httpx.get('https://api.telegram.org/',timeout=8).status_code)"  # URLError
docker exec nanolika-bot python -c "import urllib.request as u;op=u.build_opener(u.ProxyHandler({'https':'http://tunnel:8888'}));print(op.open('https://api.telegram.org/',timeout=15).status)"  # 200
# token stored as a hash only
docker exec cloud-relay-1 python -c "import sqlite3;c=sqlite3.connect('/srv/nanomuse-cloud/data/cloud.db');print([len(r[0]) for r in c.execute('SELECT token_hash FROM bot_projects')])"  # все значения 64
# no body in logs
docker logs cloud-relay-1 2>&1 | grep -c "bot[0-9]\{4,\}:"                                 # 0
```

## 9. Kill switch and incidents

| Situation | Action |
|-----------|--------|
| Tenant unpaid | `require_plan` returns 403; worker closed; nothing queued. Data stays on their machine |
| Tenant abused the gateway (spam, illegal content through their bot) | disable the project (`enabled = 0`), revoke the account's session tokens, keep the audit rows |
| Tunnel down | Telegram path fails everywhere; restart the sidecar; check the Riga proxy and the SSH key |
| Suspected leak of our egress | rotate the SSH key, the tinyproxy allow-list and `CLOUD_SECRET` |
| Riga host lost | move the tunnel to another non-RF exit; the image only needs an SSH key and a proxy address |

## 10. Anti-patterns

- storing the bot token in plaintext, "so we can debug";
- logging webhook bodies or Telegram responses that contain message text;
- letting a tenant pass us a proxy URL or a relay URL;
- gating the Telegram path with a flag the client sends;
- publishing the tunnel port and "closing it with ufw" (docker's published ports bypass ufw's INPUT chain);
- putting secrets in `docker-compose.yml` instead of an env file with mode 600;
- adding a second plan check in the client: it is unauthenticated advice, not enforcement.

## 11. Open questions

Answered on 2026-10-23: the bot lives on the client's computer, nothing of it is stored on the server, and a
lapsed plan stops only our Telegram path (the client keeps the bot and may host it elsewhere).

Still open:

1. Is one bot per account enough, or do people need several? (Current limit: three.)
2. Do client-side model calls from bots also go through the relay, or may a tenant plug their own key? This
   decides whether the plan carries model spend too, and it is the difference between a paid service and a
   free pipe.
3. Delivery transit is personal data processing under 152-ФЗ; do we document it on the privacy page, and do we
   state a zero-retention policy explicitly?
