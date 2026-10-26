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

// 3a. Голосовой ввод падал с «External buffers are not allowed». Виновата не отправка и не
//     распознавание, а сама запись: MediaRecorder отдаёт Blob, а чтение Blob в Electron даёт
//     ArrayBuffer на внешней памяти Blink, и Chromium отказывается его принимать. Копия через
//     `slice(0)` не помогала — она делалась уже после отказа (прежний патч лечил не то место,
//     микрофон не работал ни разу при зелёной сборке). FileReader тоже не помог: отказ остался.
//
//     Поэтому Blob из записи убран совсем: звук берётся сэмплами `AudioContext` — это обычная
//     память, `decodeAudioData` больше не нужен. Тот же способ, что в нашем `client/voice.ts`.
//
//     Как это видно: клиент подписывает шаги (`stage:decode:…`), а ошибка приходила без
//     префикса — значит отказ был до `stage`, на чтении записи.
//
//     Идемпотентно: повторный проход узнаёт свои вставки и ничего не меняет. Если форма кода
//     пакета изменится так, что замены не будет, сборка печатает предупреждение — молчать
//     нельзя, иначе микрофон снова окажется сломанным в готовом установщике.
const voiceClient = join(root, "node_modules", "@deepseek-ai", "dsh-experimental-client-ui-voice-input", "lib", "client.js");
if (existsSync(voiceClient)) {
  let voice = readFileSync(voiceClient, "utf8");
  const missed = [];
  const tab = "\t";

  // (1) Начать собирать сэмплы рядом с уже созданным анализатором.
  if (!voice.includes("this.pcm = [];")) {
    const anchor = `${tab.repeat(5)}this.context.createMediaStreamSource(stream).connect(this.analyser);\n`;
    if (voice.includes(anchor)) {
      voice = voice.replace(
        anchor,
        anchor +
          `${tab.repeat(5)}// NanoLika: пишем звук сами. MediaRecorder отдаёт Blob, а чтение Blob в Electron\n` +
          `${tab.repeat(5)}// даёт внешний буфер, который Chromium не принимает («External buffers are not\n` +
          `${tab.repeat(5)}// allowed»). Сэмплы AudioContext лежат в обычной памяти — их и берём.\n` +
          `${tab.repeat(5)}this.pcm = [];\n` +
          `${tab.repeat(5)}this.processor = this.context.createScriptProcessor(4096, 1, 1);\n` +
          `${tab.repeat(5)}this.processor.onaudioprocess = (event) => {\n` +
          `${tab.repeat(6)}if (this.lifetime.signal.aborted) return;\n` +
          `${tab.repeat(6)}this.pcm.push(Float32Array.from(event.inputBuffer.getChannelData(0)));\n` +
          `${tab.repeat(5)}};\n` +
          `${tab.repeat(5)}this.context.createMediaStreamSource(stream).connect(this.processor);\n` +
          `${tab.repeat(5)}this.processor.connect(this.context.destination);\n`,
      );
    } else missed.push("начало записи (createMediaStreamSource → analyser)");
  }

  // (2) Blob больше не нужен.
  const blobLines =
    `${tab.repeat(5)}const blob = new Blob(this.chunks, { type: recorder.mimeType });\n` +
    `${tab.repeat(5)}if (blob.size === 0) throw new RecordingError("empty");\n`;
  voice = voice.replace(blobLines, "");

  // (3) Вместо чтения Blob и декодирования — AudioBuffer из сэмплов.
  const builds =
    `${tab.repeat(5)}// Декодировать нечего: сэмплы уже здесь. Форма буфера та же, что давал decode.\n` +
    `${tab.repeat(5)}const parts = this.pcm ?? [];\n` +
    `${tab.repeat(5)}let collected = 0;\n` +
    `${tab.repeat(5)}for (const part of parts) collected += part.length;\n` +
    `${tab.repeat(5)}if (collected === 0) throw new RecordingError("empty");\n` +
    `${tab.repeat(5)}const merged = new Float32Array(collected);\n` +
    `${tab.repeat(5)}let at = 0;\n` +
    `${tab.repeat(5)}for (const part of parts) {\n` +
    `${tab.repeat(6)}merged.set(part, at);\n` +
    `${tab.repeat(6)}at += part.length;\n` +
    `${tab.repeat(5)}}\n` +
    `${tab.repeat(5)}const decoded = new AudioBuffer({ length: collected, numberOfChannels: 1, sampleRate: context.sampleRate });\n` +
    `${tab.repeat(5)}decoded.copyToChannel(merged, 0);\n`;
  if (!voice.includes("Декодировать нечего")) {
    const at5 = tab.repeat(5);
    const forms = [
      // чистый пакет: чтение прямо в вызове
      `${at5}const decoded = await context.decodeAudioData(await blob.arrayBuffer());\n`,
      // пакет под своим stage("decode", …)
      `${at5}const decoded = await stage("decode", () => context.decodeAudioData(await blob.arrayBuffer()));\n`,
      `${at5}const decoded = await stage("decode", () => context.decodeAudioData((await blob.arrayBuffer()).slice(0)));\n`,
      // уже читали запись отдельной строкой (наши прежние правки)
      `${at5}const raw = await blob.arrayBuffer();\n` +
        `${at5}const decoded = await stage("decode", () => context.decodeAudioData(raw.slice(0)));\n`,
      `${at5}const raw = await recordingBytes(blob);\n` +
        `${at5}const decoded = await stage("decode", () => context.decodeAudioData(raw.slice(0)));\n`,
      `${at5}const raw = await blob.arrayBuffer();\n` +
        `${at5}const decoded = await context.decodeAudioData(raw.slice(0));\n`,
      `${at5}const raw = await recordingBytes(blob);\n` +
        `${at5}const decoded = await context.decodeAudioData(raw.slice(0));\n`,
    ];
    const form = forms.find((candidate) => voice.includes(candidate));
    if (form) voice = voice.replace(form, builds);
    else missed.push("декодирование записи (decodeAudioData)");
  }

  // Прежняя правка держала чтение Blob через FileReader; теперь она не нужна и только путает.
  if (voice.includes("async function recordingBytes(blob)")) {
    voice = voice.replace(
      /\n\/\/ NanoLika: Blob\.arrayBuffer\(\)[^\n]*\n\/\/[^\n]*\nasync function recordingBytes\(blob\) \{[\s\S]*?\n\}\n/,
      "",
    );
    if (voice.includes("recordingBytes")) console.warn("patch-dsh: помощник recordingBytes остался в голосовом вводе");
  }

  // (3b) AudioContext может проснуться в состоянии suspended — тогда событий записи не будет
  //      вовсе, и человек увидит «ничего не записалось». Будим явно.
  if (!voice.includes("this.context.resume()")) {
    const wake = `${tab.repeat(5)}this.processor.connect(this.context.destination);\n`;
    if (voice.includes(wake)) voice = voice.replace(wake, wake + `${tab.repeat(5)}void this.context.resume().catch(() => undefined);\n`);
    else missed.push("запуск AudioContext после подключения процессора");
  }

  // (4) Отпустить процессор вместе с потоком.
  if (!voice.includes("this.processor.disconnect()")) {
    const releaseAnchor = `${tab.repeat(4)}this.analyser = void 0;\n`;
    if (voice.includes(releaseAnchor)) {
      voice = voice.replace(
        releaseAnchor,
        releaseAnchor +
          `${tab.repeat(4)}if (this.processor) {\n` +
          `${tab.repeat(5)}try {\n` +
          `${tab.repeat(6)}this.processor.disconnect();\n` +
          `${tab.repeat(5)}} catch {}\n` +
          `${tab.repeat(5)}this.processor = void 0;\n` +
          `${tab.repeat(4)}}\n`,
      );
    } else missed.push("освобождение процессора (this.analyser = void 0)");
  }

  writeFileSync(voiceClient, voice);
  if (missed.length === 0) console.log("patch-dsh: микрофон пишет звук сэмплами AudioContext, без Blob");
  else
    console.error(
      "patch-dsh: ВНИМАНИЕ — голосовой ввод пропатчен не полностью, микрофон в этой сборке не проверен.\n" +
        "  Не нашла: " + missed.join("; ") + "\n" +
        "  Пакет @deepseek-ai/dsh-experimental-client-ui-voice-input изменился. Посмотрите\n" +
        "  " + voiceClient + " и поправьте patch-dsh.mjs.",
    );
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
