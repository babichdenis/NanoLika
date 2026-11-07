# Marosensor failover — что где лежит

## Статическая копия

- `/srv/marosensor-mirror/current` — symlink на свежий релиз.
- `/srv/marosensor-mirror/releases/<timestamp>/marosensor.ru/` — страницы.
- `/opt/marosensor-mirror-refresh.py` — обновление копии (wget + чистка ссылок + баннер).
  Запускать вручную или таймером, когда хостинг не банит наш IP.
- Контейнер `marosensor-mirror` (nginx:alpine, `--network host`) слушает
  `127.0.0.1:8897`. Живой хостинг — primary, при ошибке отдаёт копию с
  заголовком `X-Marosensor-Mirror: fallback` и `X-Robots-Tag: noindex`.
- `/etc/marosensor-mirror/nginx.conf` — конфиг nginx.

## Вотчдог

- `/usr/local/bin/marosensor-watchdog` — проверка origin + публичного сайта.
- `/etc/marosensor-watchdog.env` — токен Telegram, SMTP, адреса (mode 600).
- `/usr/local/lib/marosensor-watchdog/sendmail.py` — отправка письма.
- systemd: `marosensor-watchdog.service` + `.timer` (раз в минуту).
  Сейчас timer выключен, пока не заполнены каналы.

Состояния: `ok`, `mirror`, `degraded`, `blocked`, `down`.
`blocked` — сайт у посетителей открывается, но наш сервер хостинг не пускает.

## Туннель через Ригу (резервный канал)

- `/usr/local/bin/marosensor-origin-tunnel` — HTTP CONNECT через
  `127.0.0.1:18888` к origin. Слушает `127.0.0.1:8443` (сейчас выключен).
- Нужен, если хостинг блокирует IP VPS, но пускает рижский прокси.
- systemd: `marosensor-origin-tunnel.service`.

## Переключение DNS

Пока A-записи `marosensor.ru` и `www` смотрят на `141.8.192.217` (sprinthost).
Для автопереключения нужно:

1. Разблокировать `151.248.123.86` в поддержке sprinthost.
2. Поменять A на `151.248.123.86`, TTL 300.
3. Добавить в Caddy:

```
marosensor.ru, www.marosensor.ru {
    reverse_proxy 127.0.0.1:8897
}
```

4. Перезагрузить Caddy, проверить HTTPS и форму.

Без доступа к DNS автоматическое переключение невозможно.
