#!/usr/bin/env node
// Prepares the staged dsh tree for our build, after `prepare-dsh` and before electron-builder:
//
//  1. Vendors the community Russian pack (`harness/vendor/dsh-locale-ru`) into
//     `dsh/node_modules/@absolutemikhail/dsh-locale-ru`, so the profile can link to it and the
//     Loader finds it next to the harness's own packages. The desktop profile lists it as a
//     bundle (`LOCALE_BUNDLE` in src/main.ts) and links it in on every start.
//  2. Keeps the built-in locale list at its stock `["zh", "en"]`: the pack registers `ru`
//     through `locale.addLanguage()`, and a second registration of the same id throws
//     ("locale \"ru\" is already registered"). An earlier build patched that list instead;
//     the line is normalised here so such a tree still builds.
//
// Idempotent: a second run copies the same files and finds nothing to normalise.

import { cpSync, existsSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appDir = resolve(here, "..");
const args = process.argv.slice(2);
const at = args.indexOf("--dir");
const root = resolve(appDir, at >= 0 ? args[at + 1] : "dsh");
const vendor = resolve(appDir, "..", "vendor", "dsh-locale-ru");

if (!existsSync(join(root, "node_modules"))) {
  console.error(`patch-dsh: ${root} has no node_modules — run prepare-dsh first`);
  process.exit(1);
}

// 1. The pack, next to the harness's own packages.
if (!existsSync(join(vendor, "package.json"))) {
  console.error(`patch-dsh: the vendored pack is missing at ${vendor}`);
  process.exit(1);
}
const dest = join(root, "node_modules", "@absolutemikhail", "dsh-locale-ru");
rmSync(dest, { recursive: true, force: true });
cpSync(vendor, dest, { recursive: true });
console.log(`patch-dsh: locale pack → ${dest}`);

// 2. Stock locale ids (the pack adds ru itself).
const lib = join(root, "node_modules", "@deepseek-ai", "dsh-client-locale", "lib");
const PATCHED = 'const LOCALE_IDS = ["zh", "en", "ru"];';
const STOCK = 'const LOCALE_IDS = ["zh", "en"];';
let normalised = 0;
for (const name of ["index.js", "client.js"]) {
  const path = join(lib, name);
  if (!existsSync(path)) continue;
  const text = readFileSync(path, "utf8");
  if (!text.includes(PATCHED)) continue;
  writeFileSync(path, text.split(PATCHED).join(STOCK));
  normalised += 1;
}
if (normalised > 0) console.log(`patch-dsh: locale ids normalised in ${normalised} file(s)`);

// 3. Russian is the product's language: with no stored preference the client picks ru, not the
//    system language. An explicit choice in Settings → Language still wins (the preference is
//    read before this fallback).
const clientPath = join(lib, "client.js");
if (existsSync(clientPath)) {
  const text = readFileSync(clientPath, "utf8");
  const from = 'return detectBrowserLocale(locales, languages) ?? "en";';
  const to = '/* NanoLika: ru by default; a stored preference is applied above this fallback. */ return "ru";';
  if (text.includes(from)) {
    writeFileSync(clientPath, text.replace(from, to));
    console.log("patch-dsh: default locale → ru");
  }
}

// 3a. Голосовой ввод падал с «External buffers are not allowed». Падает не декодирование,
//     а само чтение записи: в Electron `Blob.arrayBuffer()` отдаёт ArrayBuffer на внешней
//     памяти Blink, и Chromium отказывается работать с таким буфером. Поэтому копия через
//     `slice(0)` не помогала — она делалась уже после отказа (прежний патч лечил не то
//     место, и микрофон оставался сломанным). Здесь чтение идёт через `FileReader`, чей
//     результат копируется в обычный ArrayBuffer: `decodeAudioData` получает свой буфер и
//     отвечает текстом.
//
//     Патч не привязан к тому, как пакет оборачивает вызов (`await blob.arrayBuffer()`
//     стоит то прямо в `decodeAudioData`, то в отдельной строке под `stage("decode", …)`),
//     и не зависит от имён переменных вокруг. Повторный проход ничего не делает: чтение
//     уже идёт через `recordingBytes`.
const voiceClient = join(root, "node_modules", "@deepseek-ai", "dsh-experimental-client-ui-voice-input", "lib", "client.js");
if (existsSync(voiceClient)) {
  const text = readFileSync(voiceClient, "utf8");
  const HELPER = "recordingBytes";
  const bytesHelper = [
    "",
    "// NanoLika: Blob.arrayBuffer() в Electron даёт внешний буфер, который Chromium не принимает",
    "// («External buffers are not allowed»). FileReader отдаёт обычный, копия его закрепляет.",
    "async function recordingBytes(blob) {",
    "\tconst read = await new Promise((resolve, reject) => {",
    "\t\tconst reader = new FileReader();",
    "\t\treader.onload = () => resolve(reader.result);",
    "\t\treader.onerror = () => reject(reader.error ?? new Error('the recording could not be read'));",
    "\t\treader.readAsArrayBuffer(blob);",
    "\t});",
    "\t// slice() отдаёт копию в обычной памяти — с ней decodeAudioData уже работает.",
    "\treturn new Uint8Array(read).slice().buffer;",
    "}",
    "",
  ].join("\n");
  if (!text.includes(HELPER)) {
    const reads = text.split("await blob.arrayBuffer()").length - 1;
    if (reads > 0) {
      writeFileSync(voiceClient, text.split("await blob.arrayBuffer()").join("await recordingBytes(blob)") + bytesHelper);
      console.log(`patch-dsh: голосовой ввод читает запись без внешнего буфера (${reads})`);
    } else {
      // Ни починить, ни подтвердить: код пакета изменился, и фикса в нём нет. Молчать
      // нельзя — так микрофон уже один раз остался сломанным при зелёной сборке.
      console.error(
        "patch-dsh: ВНИМАНИЕ — в голосовом вводе не нашла чтения записи из Blob.\n" +
          "  Пакет @deepseek-ai/dsh-experimental-client-ui-voice-input изменился, микрофон\n" +
          "  в этой сборке не проверен. Посмотрите " + voiceClient + " и поправьте патч.",
      );
    }
  }
}

// 3b. The right Sidebar's Browser tab is gated on the profile being named `desktop` in the
//     web-app bundle; our profile is `nanolika`, so the bundle switched it off and the tab
//     never appeared. The row is enabled here, in the bundle, so a fresh install and a rebuilt
//     dsh carry it — the profile patch on top of it is then a duplicate, not the only copy.
//     Telemetry and product analytics share that gate and stay off: they are not ours to send.
const webApp = join(root, "node_modules", "@deepseek-ai", "dsh-web-app", "cordis.patch.yml");
if (existsSync(webApp)) {
  const text = readFileSync(webApp, "utf8");
  const gated = /(- id: ui-sidebar-browser\n\s+name: '@deepseek-ai\/dsh-client-ui-sidebar-browser'\n\s+disabled: )!!js "[^"]*profileContext[^"]*"/;
  if (gated.test(text)) {
    writeFileSync(
      webApp,
      text.replace(
        gated,
        // no trailing comment: a `#` after the value breaks the YAML mapping
        "$1false",
      ),
    );
    console.log("patch-dsh: right-sidebar browser enabled");
  }
}

// 4. The boot splash of the web client prints the wordmark "HARNESS" while plugins load; our
//    product's name goes there instead ("NanoLika"). The asset is minified, so the search is
//    the stable shape around the literal, not its variable names.
const frontend = join(root, "node_modules", "@deepseek-ai", "dsh-web-frontend", "dist", "assets");
if (existsSync(frontend)) {
  for (const name of readdirSync(frontend)) {
    if (!name.endsWith(".js")) continue;
    const path = join(frontend, name);
    const text = readFileSync(path, "utf8");
    if (!/wordmark\s*,\s*"HARNESS"/.test(text)) continue;
    writeFileSync(path, text.replace(/wordmark\s*,\s*"HARNESS"/g, 'wordmark,"NanoLika"'));
    console.log(`patch-dsh: splash wordmark → NanoLika (${name})`);
  }
}
