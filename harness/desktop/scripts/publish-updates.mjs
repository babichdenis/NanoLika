#!/usr/bin/env node
/**
 * Выложить сборки десктопа на зеркало обновлений.
 *
 *   node scripts/publish-updates.mjs --dist=dist                 # mac + win, что нашлось
 *   node scripts/publish-updates.mjs --dist=dist --only=mac      # только macOS
 *   node scripts/publish-updates.mjs --dist=dist --dry-run       # показать, что поедет
 *
 * Что делает:
 *   Windows — берёт `latest.yml`, установщик `.exe` и `.exe.blockmap` из dist и кладёт
 *   как есть в `/dl/win/`: это ровно то, что читает electron-updater, версию и SHA-512
 *   он берёт из latest.yml, поэтому ничего не пересчитывается.
 *   macOS — электронный апдейтер у нас не настроен, поэтому собирается `latest.json`
 *   (версия, дата, заметки и по файлу на архитектуру с SHA-256), который читает
 *   `update-lika`.
 *
 * Сервер по умолчанию — тот, что раздаёт cloud.nanolika.ru (Caddy, каталог /dl).
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync, mkdtempSync } from "node:fs";
import { basename, join } from "node:path";
import { tmpdir } from "node:os";

const argv = process.argv.slice(2);
const args = {};
for (let i = 0; i < argv.length; i += 1) {
  const [k, v] = argv[i].replace(/^--/, "").split("=");
  // `--notes "текст"` и `--notes=текст` значат одно и то же
  if (v === undefined && argv[i + 1] && !argv[i + 1].startsWith("--")) {
    args[k] = argv[i + 1];
    i += 1;
  } else {
    args[k] = v ?? true;
  }
}
const dist = args.dist || "dist";
const server = args.server || "root@151.248.123.86";
const remoteDir = args["remote-dir"] || "/opt/nanomuse/cloud/dl";
const publicBase = args["public-base"] || "https://cloud.nanolika.ru/dl";
const only = args.only ? String(args.only).split(",") : ["mac", "win"];
const dry = Boolean(args["dry-run"]);

if (!existsSync(dist)) {
  console.error(`нет каталога сборки: ${dist}`);
  process.exit(1);
}

const files = readdirSync(dist);
const sha256 = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
const size = (path) => statSync(path).size;

/** Версию берём из имени установщика, иначе из latest.yml, иначе из package.json рядом. */
function detectVersion() {
  const fromName = files.map((f) => f.match(/(\d+\.\d+\.\d+)/)).find(Boolean);
  if (fromName) return fromName[1];
  if (files.includes("latest.yml")) {
    const m = readFileSync(join(dist, "latest.yml"), "utf8").match(/^version:\s*(\S+)/m);
    if (m) return m[1];
  }
  const pkg = join("..", "package.json");
  if (existsSync(pkg)) return JSON.parse(readFileSync(pkg, "utf8")).version;
  throw new Error("не удалось определить версию");
}

const version = String(args.version || detectVersion());
const uploads = [];

// --- Windows: файлы electron-updater ---
if (only.includes("win")) {
  const yml = files.find((f) => f === "latest.yml");
  const exe = files.find((f) => f.endsWith(".exe"));
  if (!yml || !exe) {
    console.log("Windows: в сборке нет latest.yml и .exe — пропускаю");
  } else {
    const blockmap = files.find((f) => f.endsWith(".exe.blockmap"));
    for (const f of [yml, exe, blockmap].filter(Boolean)) {
      uploads.push({ from: join(dist, f), to: `${remoteDir}/win/${f}` });
    }
  }
}

// --- macOS: собственный манифест ---
if (only.includes("mac")) {
  const macFiles = files.filter((f) => /mac-(x64|arm64)\.(zip|dmg)$/.test(f));
  if (macFiles.length === 0) {
    console.log("macOS: нет сборок *-mac-<arch>.zip/.dmg — пропускаю");
  } else {
    const assets = {};
    for (const f of macFiles.sort((a, b) => Number(b.endsWith(".zip")) - Number(a.endsWith(".zip")))) {
      const arch = f.includes("arm64") ? "arm64" : "x64";
      const asset = (assets[arch] ??= {});
      if (f.endsWith(".zip")) {
        // zip ставит update-lika: он и есть «адрес обновления» для этой архитектуры
        if (asset.url) continue;
        asset.url = f;
        asset.sha256 = sha256(join(dist, f));
        asset.size = size(join(dist, f));
      } else {
        // dmg — то, что человек скачивает со страницы /download/ (site/download.js)
        if (asset.dmg) continue;
        asset.dmg = f;
        asset.dmgSize = size(join(dist, f));
      }
      uploads.push({ from: join(dist, f), to: `${remoteDir}/mac/${f}` });
    }
    const manifest = {
      version,
      released: new Date().toISOString(),
      notes: args.notes || "",
      assets,
    };
    const tmp = mkdtempSync(join(tmpdir(), "nl-manifest-"));
    const path = join(tmp, "latest.json");
    writeFileSync(path, JSON.stringify(manifest, null, 2) + "\n");
    uploads.push({ from: path, to: `${remoteDir}/mac/latest.json` });
  }
}

if (uploads.length === 0) {
  console.log("нечего выкладывать");
  process.exit(0);
}

console.log(`версия ${version}, файлов ${uploads.length}:`);
for (const u of uploads) console.log(`  ${basename(u.from)} → ${u.to}`);
if (dry) {
  console.log("пробный запуск: ничего не отправлено");
  process.exit(0);
}

// каталоги создаём заранее: scp не умеет mkdir
execFileSync("ssh", [server, `mkdir -p ${remoteDir}/win ${remoteDir}/mac`], { stdio: "inherit" });
for (const u of uploads) {
  execFileSync("scp", ["-q", u.from, `${server}:${u.to}`], { stdio: "inherit" });
  console.log(`  загружено ${basename(u.to)}`);
}
// --- index.json: what the app's own check reads (nanolika.ru/dl/index.json) -----------
// The host asks for this file once a day: `{releases: [{tag, page, assets: [{name, url,
// size}]}]}` (harness/dsh-nanomuse/src/desk.ts, checkForUpdate). The newest release is the
// first one with a tag, and it holds every installer of that version — both platforms come
// from the mirror itself, so a publish of one platform does not drop the other's file.
const wanted = /\.(dmg|zip|exe)$/i;
const listing = execFileSync(
  "ssh",
  [server, `find ${remoteDir}/mac ${remoteDir}/win -maxdepth 1 -type f -name '*${version}*' -printf '%p\t%s\n'`],
  { encoding: "utf8" },
);
const assets = listing
  .split("\n")
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line) => {
    const [path, bytes] = line.split("\t");
    return { name: basename(path), url: path.replace(remoteDir, publicBase), size: Number(bytes) || 0 };
  })
  .filter((a) => wanted.test(a.name) && !/blockmap/i.test(a.name))
  .sort((a, b) => a.name.localeCompare(b.name));
const index = {
  repo: args.repo || "babichdenis/NanoLika",
  releases: [{ tag: `v${version}`, page: args.page || "https://nanolika.ru/download/", assets }],
};
const tmpIndex = mkdtempSync(join(tmpdir(), "nl-index-"));
const indexPath = join(tmpIndex, "index.json");
writeFileSync(indexPath, JSON.stringify(index, null, 2) + "\n");
execFileSync("scp", ["-q", indexPath, `${server}:${remoteDir}/index.json`], { stdio: "inherit" });
console.log(`  загружено index.json (${assets.length} файлов)`);
const manifestUrl = `${publicBase}/mac/latest.json`;
console.log(`\nготово. Проверка:\n  curl -s ${publicBase}/win/latest.yml | head -3\n  curl -s ${manifestUrl}\n  curl -s ${publicBase}/index.json`);
