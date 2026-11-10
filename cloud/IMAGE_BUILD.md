# Пересборка образов релея и воркера

Правило: **код живёт в образе, а не в bind-mount'ах**. Файловые оверрайды в
`/opt/nanomuse/cloud/overrides/**` больше не монтируются в контейнеры — всё, что в них
было, запекли в образ `nanomuse-cloud:20261110-v2`. В контейнере релея остались только
данные и статика:

```
/srv/nanomuse-cloud/data
/srv/models
/srv/nanomuse-cloud/nanomuse_cloud/web2   (сборка веб-клиента)
/run/model-gateway
```

## Релей

Сборка из репозитория (единственный правильный путь):

```bash
cd cloud
docker build -t nanomuse-cloud:$(date +%Y%m%d) .
```

⚠️ **Пока не запускать на сервере:** репозиторий и живой образ разошлись в 23 файлах
(`service.py`, `hub.py`, `config.py`, `oauth.py`, `payment_audit.py`, консоль админки и
др.). Сборка из git сейчас откатит часть продовых правок. Сначала нужно свести эти
файлы в репозиторий (см. «Что осталось»), затем сборка из git становится безопасной.

Пока паритет не сведён, образ собирается «из живого состояния» — ровно то, что
работает сейчас:

```bash
# на сервере, из репозитория на Mac этот скрипт копируется вместе с контекстом
python3 /tmp/prepare_build2.py      # вынимает пакет из текущего образа и накладывает
                                    # все смонтированные файлы (последний выигрывает)
cd /opt/nanomuse/cloud/builds/relay-<дата>
cp /opt/nanomuse/cloud/Dockerfile .
docker build -t nanomuse-cloud:<дата>-v2 .
```

Затем в `docker-compose.override.yml`:

```yaml
  relay:
    image: nanomuse-cloud:<дата>-v2
```

и

```bash
cd /opt/nanomuse/cloud && docker compose up -d --no-deps relay
```

Новые файловые оверрайды добавлять **нельзя**: правка идёт в репозиторий → пересборка
образа → `up -d`.

## Воркер

Базовый образ `nanomuse:worker-20261105r` собран не из этого репозитория, поэтому
пересборка — слоем сверху, из файлов репозитория:

```bash
tmp=$(mktemp -d)
cp nanomuse/server/accounts.py "$tmp/accounts.py"
cat > "$tmp/Dockerfile" <<'EOF'
FROM nanomuse:worker-20261105r
COPY accounts.py /app/nanomuse/server/accounts.py
EOF
docker build -t nanomuse:worker-$(date +%Y%m%d) "$tmp"
```

В compose-оверрайде у `cloud-worker` — `image: nanomuse:worker-<дата>`; из `volumes`
остаётся только `/opt/nanomuse/cloud/data/worker:/data`.

## Проверка после пересборки

```bash
curl -s https://cloud.nanolika.ru/healthz
curl -s -H "Authorization: Bearer <ключ>" https://cloud.nanolika.ru/v1/me
curl -s -H "Authorization: Bearer <ключ>" https://cloud.nanolika.ru/api/hub
curl -s -o /dev/null -w '%{http_code}\n' https://cloud.nanolika.ru/web2/
curl -s -o /dev/null -w '%{http_code}\n' https://cloud.nanolika.ru/app/admin/
docker logs --since 5m cloud-relay-1 2>&1 | grep -iE 'Traceback|ImportError'
docker logs --since 5m cloud-worker 2>&1 | grep -iE 'bad_key|Traceback'
```

И одно сообщение в чат Лики: `POST /api/threads/main/send` → ответ `assistant`.

## Что осталось (следующий шаг)

Свести живой код в репозиторий, чтобы `docker build` из `cloud/` давал прод. Расхождения
на дату 2026-11-10 (23 файла), направление разное — где-то живое новее, где-то репозиторий:

```
service.py  hub.py  config.py  oauth.py  sync.py  documents.py  payment_audit.py
prices_admin.py  price_drafts.py  deepseek_pricing.py  reference_catalog.py
menu_admin.py  console/*  web_client/account/payments.js
```

После сведения: `docker build -t nanomuse-cloud:<дата> .` — и оверрайды больше не нужны
нигде.
