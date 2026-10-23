# Moving NanoLika Cloud to a server in Russia

The relay keeps personal data: sign-in codes and identifiers (`codes`, `api_keys`), profiles,
devices, synced conversations (`sync_conversations`, `sync_messages`), the ledger and the
acquiring orders (`tbank_orders`). Russian law (152-ФЗ and 242-ФЗ) requires that data of
Russian citizens is stored in Russia, so the relay and its backups belong on a Russian
server. This page is the runbook for that move; `scripts/cloud-migrate.sh` does the copying.

What stays abroad: only the Telegram egress. `api.telegram.org` is unreachable from Russia,
so the bot reaches it through a proxy on the old machine. A proxy passes traffic and stores
nothing; no database, no `.env`, no backup may stay behind.

## What the move needs

| part | memory (measured) | disk |
|---|---|---|
| relay | 69 MB | image 269 MB, database 1.6 MB |
| Caddy (TLS, reverse proxy) | 18 MB | 93 MB |
| new-api (the model gateway, if you run one) | 93 MB | 328 MB |
| site chat | 50 MB | under 1 MB |
| bot | about 100 MB | 232 MB |

That is roughly 330 MB in use, and 5 to 6 GB of disk with the system. Two cores and 2 GB are
the sensible floor: five containers fit in 1 GB, but building the relay image on the machine
(which every update needs) does not. 40 GB of disk leaves room for backups and logs.

## Steps

### 0. Before the day (a day ahead)

Lower the TTL of the records that will change to 300 seconds, so the switch is quick to
undo: `cloud.nanolika.ru` (relay) and, if the site moves too, `nanolika.ru` and `www`.

### 1. Prepare the new server

Ubuntu 24.04, SSH by key only, and then:

```sh
adduser --disabled-password --gecos "" nanolika
usermod -aG sudo nanolika
# key for the person who administers it, then: PasswordAuthentication no
apt update && apt -y upgrade
apt -y install ufw fail2ban curl git docker.io docker-compose-v2 sqlite3
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
ufw allow 22,80,443/tcp && ufw --force enable
```

`sqlite3` matters: `cloud-migrate.sh` uses it to dump the database safely.

### 2. Put the code in place

```sh
git clone https://github.com/babichdenis/NanoLika.git /opt/nanomuse
cd /opt/nanomuse/cloud
bash ../scripts/self-host.sh --no-start   # writes .env with fresh secrets; keep the old .env instead
```

Do **not** let the script write a new `.env` for the live move: `restore` brings the old one,
and the accounts, the model keys and the payment settings come with it.

### 3. Take the archive from the old server

```sh
cd /opt/nanomuse
bash scripts/cloud-migrate.sh backup root@46.8.228.71 --out cloud-backup-$(date +%F).tar.gz
tar tzf cloud-backup-*.tar.gz        # expect cloud/.env, cloud/Caddyfile, cloud/data/, backups/
```

The archive is mode 600 because it holds the relay's secrets and the database. Move it with
`scp`, never through a chat or a mail box, and delete the local copy after the move.

### 4. Restore on the new server

```sh
bash scripts/cloud-migrate.sh restore root@NEW.HOST cloud-backup-2026-10-23.tar.gz
```

It unpacks into `/opt/nanomuse` (`cloud/` and `backups/`), renames the dumped database into
place, runs `docker compose up -d` and waits for `/healthz` on `127.0.0.1:8787`.

### 5. Check before DNS, not after

Add a temporary name to `/etc/hosts` on your own machine, or ask curl directly:

```sh
curl -s --resolve cloud.nanolika.ru:443:NEW.IP https://cloud.nanolika.ru/healthz
bash scripts/cloud-migrate.sh verify https://cloud.nanolika.ru   # after DNS
```

The relay also needs its door to the world: port 80 for the certificate and 443 for traffic.
Caddy fetches the certificate during the first request to the new address, so make that
request yourself rather than waiting for a visitor to make it.

### 6. Switch DNS

Point `cloud.nanolika.ru` at the new IP, wait for the TTL, then:

```sh
bash scripts/cloud-migrate.sh verify https://cloud.nanolika.ru
```

### 7. Move the bot

The bot goes to the new server as well: its configuration holds the account and the
conversation history. It keeps reaching Telegram through the proxy on the old machine
(`HTTPS_PROXY` in its unit, or the proxy setting in `config.toml`). Check after the move
that a message to the bot is answered and that the answer comes from the new server.

### 8. Wipe the old server

In this order, and only after `verify` passes:

```sh
docker compose -f /opt/nanomuse/cloud/docker-compose.yml down
rm -f /opt/nanomuse/cloud/data/cloud.db* /opt/nanomuse/cloud/.env*
rm -rf /opt/nanomuse/backups
docker system prune -a --volumes
```

Then remove the old server's SSH keys from your machine, and leave only the proxy running.
A copy of the database left on a machine abroad is the same violation as running the relay
there.

### 9. Say it in the documents

The privacy policy and the offer must name where the data is stored. If the purchase flow
names a hosting provider, it changes with this move.

## What a person has to check by hand

- Sign-in: a code arrives by e-mail and lets the account in.
- The app: an existing account signs in and lists its devices.
- The hub: two devices see each other.
- Payment: a test order reaches the acquiring and lands in `tbank_orders`.
- The console at `/app/` opens, and the admin page opens for an admin account.

`cloud-migrate.sh verify` covers only the four public pages and the certificate: the account
side needs someone to type a code.

## Rollback

DNS back to the old IP. The old server must not be wiped before `verify` passes, so keep it
running until the end of the day of the move. The database on the new server has been read
and written by then; going back means restoring the archive from step 3 onto the old server
and pointing DNS at it.
