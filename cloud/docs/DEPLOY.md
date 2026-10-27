# Как теперь выкатывается релей

## YooKassa: текущая готовность

В исходниках есть отдельный адаптер YooKassa. Для тестового подключения задаются только
секреты окружения сервера: `PAYMENT_PROVIDER=yookassa`, `YOOKASSA_SHOP_ID` и тестовый
`YOOKASSA_SECRET_KEY` с префиксом `test_`. В кабинете YooKassa указывается webhook
`https://cloud.nanolika.ru/v1/payments/yookassa/notify` для событий `payment.succeeded`,
`payment.canceled` и `refund.succeeded`.

Уведомление YooKassa не считается подтверждением само по себе: релей сверяет платёж и
возвраты через API с авторизацией магазина. Тестовые платежи идут в отдельный тестовый
кошелёк. Для боевых ключей нужны оба явных переключателя `YOOKASSA_LIVE_ENABLED=1` и
`YOOKASSA_RECEIPTS_READY=1`; включать их нельзя, пока не проверены налоговый режим,
чек при оплате, доставка чека покупателю, возврат и отражение возврата в учёте. Нынешний
путь подготовки фискального чека ещё не подключён к созданию заказа, поэтому флаг готовности
чеков пока должен оставаться выключенным.

Ключ, отправленный в чат, следует считать раскрытым: перевыпустить его в кабинете YooKassa,
а новый сохранить только в секретах окружения сервера. Не переносить ключ в исходники,
историю Git, журналы, браузер или этот документ.

Короткое правило: **код живёт в репозитории, образ собирается из него, навесных
`docker cp` и патч-скриптов больше нет.**

## Правка → выкладка

1. Правим файлы здесь: `cloud/nanomuse_cloud/...` (в том числе `console/` — это
   статика веб-клиента и админки, она лежит внутри python-пакета).
2. Копируем изменённое на сервер в дерево сборки:

   ```bash
   scp -O cloud/nanomuse_cloud/api.py \
         root@151.248.123.86:/opt/nanomuse/cloud/nanomuse_cloud/api.py
   ```

3. Собираем образ и поднимаем контейнер:

   ```bash
   ssh root@151.248.123.86 'cd /opt/nanomuse/cloud \
     && docker compose build relay && docker compose up -d relay'
   ```

4. Проверяем:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://cloud.nanolika.ru/healthz
   curl -s https://cloud.nanolika.ru/v1/auth/oauth/providers
   ```

## Чего больше не делаем

- **Не подкладываем файлы в `/opt/nanomuse/cloud/overrides/*`.** Раньше так
  «пропатчивали» `api.py`, `db.py`, консоль и веб-клиент; файлы перезаписывали
  друг друга, и правки разных людей пропадали (вход через Яндекс и страница цен
  поставщиков отваливались именно так).
- Не запускаем `/usr/local/bin/nanomuse-patch-routes` — ручки уже в `api.py`
  строками `install_oauth(app, cloud, settings)` и
  `install_provider_prices(app, admin_dep)`.
- Не правим код внутри контейнера: следующий `up -d` это затрёт.

## Что осталось снаружи образа

| что | где | зачем |
|---|---|---|
| `.env` | `/opt/nanomuse/cloud/.env` | ключи, меню моделей, OAuth-пары |
| `docker-compose.override.yml` | там же | только `environment` и два монтирования: `./data` и каталог со снимком цен |
| `polza-proxy.conf`, сервис `polza-proxy` | там же | polza принимает ключ только в `X-API-Key`, релей шлёт `Bearer` |
| `overrides/providers-prices-20261024/.../console/providers/` | там же | `prices.json`: пишется по кнопке «Обновить данные» и (по желанию) таймером |
| `price-providers.service` | systemd | ручной/периодический сбор цен, если таймер включат |

## Проверки после выкладки

```bash
/v1/auth/oauth/providers      → 200, в списке yandex
/v1/auth/oauth/yandex/start   → 302 на oauth.yandex.ru
/v1/auth/logout               → 302 на nanolika.ru
/v1/admin/provider-prices     → 200, модели и поставщики (с X-Admin-Token)
/app/app.js                   → отдаёт кнопку аккаунта (grep nmMountAccount)
```

## Откат

Перед сборкой можно сохранить текущий образ:

```bash
docker tag cloud-relay:latest cloud-relay:backup-$(date +%y%m%d-%H%M)
```

Откат — `docker tag cloud-relay:backup-… cloud-relay:latest && docker compose up -d relay`.
