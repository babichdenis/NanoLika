// src/index.ts
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { dirname, join } from "node:path";
var name = "nanomuse-listen";
var inject = ["webServer", "speechToText"];
var VOICE_PREFIX = "/nanomuse/voice";
var SPEAK_PREFIX = "/nanomuse/speak";
var SETTINGS_PREFIX = "/nanomuse/listen";
var LIMIT = 16 * 1024 * 1024;
var DEFAULTS = { voice: "Milena", answers: false, tts: "system", ttsModel: "voice", cloudVoice: "alloy", piperVoice: "vits-piper-ru_RU-irina-medium", stt: "local", sttModel: "ear-ru", sttFallback: true };
var PIPER_VOICES = [
  { id: "vits-piper-ru_RU-irina-medium", title: "\u0418\u0440\u0438\u043D\u0430 \u2014 \u0440\u0443\u0441\u0441\u043A\u0438\u0439, \u0436\u0435\u043D\u0441\u043A\u0438\u0439", lang: "ru" },
  { id: "vits-piper-ru_RU-denis-medium", title: "\u0414\u0435\u043D\u0438\u0441 \u2014 \u0440\u0443\u0441\u0441\u043A\u0438\u0439, \u043C\u0443\u0436\u0441\u043A\u043E\u0439", lang: "ru" },
  { id: "vits-piper-ru_RU-dmitri-medium", title: "\u0414\u043C\u0438\u0442\u0440\u0438\u0439 \u2014 \u0440\u0443\u0441\u0441\u043A\u0438\u0439, \u043C\u0443\u0436\u0441\u043A\u043E\u0439", lang: "ru" },
  { id: "vits-piper-ru_RU-ruslan-medium", title: "\u0420\u0443\u0441\u043B\u0430\u043D \u2014 \u0440\u0443\u0441\u0441\u043A\u0438\u0439, \u043C\u0443\u0436\u0441\u043A\u043E\u0439", lang: "ru" },
  { id: "vits-piper-en_US-lessac-medium", title: "Lessac \u2014 \u0430\u043D\u0433\u043B\u0438\u0439\u0441\u043A\u0438\u0439, \u0436\u0435\u043D\u0441\u043A\u0438\u0439", lang: "en" },
  { id: "vits-piper-en_US-amy-medium", title: "Amy \u2014 \u0430\u043D\u0433\u043B\u0438\u0439\u0441\u043A\u0438\u0439, \u0436\u0435\u043D\u0441\u043A\u0438\u0439", lang: "en" }
];
var cloudHealth = { ok: false, checkedAt: 0, latencyMs: 0, error: "not checked yet" };
var CLOUD_RETRY_MS = 6e4;
var PIPER_DIR = "speech/tts/vits-piper-ru_RU-irina-medium";
var VOICE_LIMIT = 40;
function home() {
  return process.env.DSH_HOME !== void 0 && process.env.DSH_HOME !== "" ? process.env.DSH_HOME : join(homedir(), ".dsh");
}
function settingsPath() {
  return join(home(), "nanomuse", "listen.json");
}
async function loadSettings() {
  try {
    const raw = JSON.parse(await readFile(settingsPath(), "utf8"));
    return {
      voice: typeof raw.voice === "string" && raw.voice !== "" ? raw.voice : DEFAULTS.voice,
      answers: raw.answers === true,
      tts: raw.tts === "piper" || raw.tts === "silero" || raw.tts === "cloud" ? raw.tts : "system",
      ttsModel: typeof raw.ttsModel === "string" && raw.ttsModel !== "" ? raw.ttsModel : DEFAULTS.ttsModel,
      cloudVoice: typeof raw.cloudVoice === "string" && raw.cloudVoice !== "" ? raw.cloudVoice : DEFAULTS.cloudVoice,
      piperVoice: typeof raw.piperVoice === "string" && raw.piperVoice !== "" ? raw.piperVoice : DEFAULTS.piperVoice,
      stt: raw.stt === "cloud" ? "cloud" : "local",
      sttModel: typeof raw.sttModel === "string" && raw.sttModel !== "" ? raw.sttModel : DEFAULTS.sttModel,
      sttFallback: raw.sttFallback !== false
    };
  } catch {
    return { ...DEFAULTS };
  }
}
async function saveSettings(patch) {
  const next = { ...await loadSettings(), ...patch };
  const file = settingsPath();
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, `${JSON.stringify(next, null, 2)}
`, "utf8");
  return next;
}
function apply(ctx) {
  const handler = async (req, res) => {
    if (req.method !== "POST") return send(res, 405, { error: { code: "method", message: "POST only" } });
    try {
      const body = await readJson(req, LIMIT);
      const audioBase64 = typeof body.audio === "string" ? body.audio : "";
      const language = typeof body.language === "string" ? body.language : "ru";
      if (audioBase64 === "") return send(res, 400, { error: { code: "bad_request", message: "audio is required" } });
      const audio = new Uint8Array(Buffer.from(audioBase64, "base64"));
      const saved = await loadSettings();
      const transcription = await transcribeWithChain(ctx, audio, saved, language, req);
      send(res, 200, { text: transcription.text, via: transcription.via });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      ctx.logger.warn("nanomuse-listen: transcription failed: %s", message);
      send(res, 500, { error: { code: "internal", message } });
    }
  };
  ctx.effect(() => ctx.webServer.register({ kind: "prefix", path: VOICE_PREFIX, handler }), "nanomuse-listen: the voice route");
  const speak = async (req, res) => {
    if (req.method !== "POST") return send(res, 405, { error: { code: "method", message: "POST only" } });
    try {
      const body = await readJson(req, 64 * 1024);
      const text = typeof body.text === "string" ? body.text.trim() : "";
      if (text === "") return send(res, 400, { error: { code: "bad_request", message: "text is required" } });
      const saved = await loadSettings();
      const chosen = typeof body.provider === "string" ? body.provider : saved.tts;
      const voice = typeof body.voice === "string" && body.voice !== "" ? body.voice : saved.voice;
      const chain = [chosen];
      let lastError;
      for (const provider of chain) {
        try {
          if (provider === "piper") {
            const audio = await piperSpeak(text.slice(0, 1200), saved.piperVoice);
            return send(res, 200, { audio: audio.toString("base64"), mime: "audio/wav", voice: saved.piperVoice, provider: "piper" });
          }
          if (provider === "system") {
            const dir = await mkdtemp(join(tmpdir(), "nanomuse-speak-"));
            const file = join(dir, "say.wav");
            try {
              await run("say", ["-v", voice, "-o", file, "--data-format=LEI16@22050", "--", text.slice(0, 1200)]);
              const audio = await readFile(file);
              return send(res, 200, { audio: audio.toString("base64"), mime: "audio/wav", voice, provider: "system" });
            } finally {
              await rm(dir, { recursive: true, force: true }).catch(() => void 0);
            }
          }
          if (provider === "cloud") {
            const audio = await cloudSpeak(text.slice(0, 1200), saved.ttsModel, saved.cloudVoice);
            return send(res, 200, { audio: audio.toString("base64"), mime: "audio/wav", voice: saved.cloudVoice, provider: "cloud" });
          }
          throw new Error("Silero \u043D\u0435 \u0443\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D");
        } catch (error) {
          lastError = error;
        }
      }
      throw lastError ?? new Error("\u041D\u0438 \u043E\u0434\u0438\u043D \u0433\u043E\u043B\u043E\u0441 \u043D\u0435 \u043E\u0442\u0432\u0435\u0442\u0438\u043B");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      ctx.logger.warn("nanomuse-listen: speech synthesis failed: %s", message);
      send(res, 500, { error: { code: "internal", message } });
    }
  };
  ctx.effect(() => ctx.webServer.register({ kind: "prefix", path: SPEAK_PREFIX, handler: speak }), "nanomuse-listen: the speak route");
  const settings = async (req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const route = url.pathname.slice(SETTINGS_PREFIX.length) || "/";
    try {
      if (req.method === "GET" && route === "/settings") return send(res, 200, await loadSettings());
      if (req.method === "POST" && route === "/settings") {
        const body = await readJson(req, 8 * 1024);
        const patch = {};
        if (typeof body.voice === "string" && body.voice !== "") patch.voice = body.voice;
        if (typeof body.answers === "boolean") patch.answers = body.answers;
        if (body.tts === "system" || body.tts === "piper" || body.tts === "silero" || body.tts === "cloud") patch.tts = body.tts;
        if (typeof body.ttsModel === "string" && body.ttsModel !== "") patch.ttsModel = body.ttsModel;
        if (typeof body.cloudVoice === "string" && body.cloudVoice !== "") patch.cloudVoice = body.cloudVoice;
        if (typeof body.piperVoice === "string" && body.piperVoice !== "") patch.piperVoice = body.piperVoice;
        if (body.stt === "local" || body.stt === "cloud") patch.stt = body.stt;
        if (typeof body.sttModel === "string" && body.sttModel !== "") patch.sttModel = body.sttModel;
        if (typeof body.sttFallback === "boolean") patch.sttFallback = body.sttFallback;
        return send(res, 200, await saveSettings(patch));
      }
      if (req.method === "GET" && route === "/voices") return send(res, 200, { voices: await listVoices() });
      if (req.method === "GET" && route === "/tts/voices") {
        const saved = await loadSettings();
        const installed = new Set(await installedPiper());
        return send(res, 200, {
          selected: saved.piperVoice,
          voices: PIPER_VOICES.map((voice) => ({ ...voice, installed: installed.has(voice.id) }))
        });
      }
      if (req.method === "POST" && route === "/warmup") {
        const saved = await loadSettings();
        if (saved.tts === "piper") void piperWarmup(saved.piperVoice);
        return send(res, 200, { ok: true });
      }
      if (req.method === "POST" && route === "/tts/install") {
        const body = await readJson(req, 8 * 1024);
        const id = typeof body.id === "string" ? body.id : "";
        const voice = PIPER_VOICES.find((item) => item.id === id);
        if (voice === void 0) return send(res, 400, { error: { code: "bad_request", message: "unknown voice" } });
        await installPiper(voice.id);
        return send(res, 200, { id: voice.id, installed: true });
      }
      if (req.method === "GET" && route === "/health") {
        const saved = await loadSettings();
        return send(res, 200, {
          cloud: cloudHealth,
          stt: saved.stt === "cloud" && cloudHealth.ok ? saved.sttModel : "local",
          tts: saved.tts
        });
      }
      if (req.method === "GET" && route === "/tts") {
        return send(res, 200, {
          providers: [
            { id: "system", name: "\u0421\u0438\u0441\u0442\u0435\u043C\u043D\u044B\u0439 \u0433\u043E\u043B\u043E\u0441", local: true, ready: true, default: (await loadSettings()).tts === "system" },
            { id: "piper", name: "Piper \u0418\u0440\u0438\u043D\u0430 (\u043D\u0435\u0439\u0440\u043E\u0433\u043E\u043B\u043E\u0441, \u043B\u043E\u043A\u0430\u043B\u044C\u043D\u043E)", local: true, ready: await piperReady(), default: (await loadSettings()).tts === "piper" },
            { id: "cloud", name: "NanoLika Cloud (\u043E\u0431\u043B\u0430\u043A\u043E)", local: false, ready: (await loadSettings()).tts === "cloud", default: (await loadSettings()).tts === "cloud" }
          ]
        });
      }
      if (req.method === "GET" && route === "/stt") {
        const speech = ctx.get("speechToText");
        if (speech === void 0) return send(res, 503, { error: { code: "not_ready", message: "Speech recognition not available" } });
        return send(res, 200, speech.snapshot());
      }
      if (req.method === "POST" && route === "/stt") {
        const speech = ctx.get("speechToText");
        if (speech === void 0) return send(res, 503, { error: { code: "not_ready", message: "Speech recognition not available" } });
        const body = await readJson(req, 8 * 1024);
        const patch = {};
        if (typeof body.providerId === "string" && body.providerId !== "") patch.providerId = body.providerId;
        if (typeof body.language === "string") patch.language = body.language;
        await speech.configure(patch);
        return send(res, 200, speech.snapshot());
      }
      if (req.method === "POST" && route === "/stt/prepare") {
        const speech = ctx.get("speechToText");
        if (speech === void 0) return send(res, 503, { error: { code: "not_ready", message: "Speech recognition not available" } });
        const body = await readJson(req, 8 * 1024);
        const id = typeof body.providerId === "string" ? body.providerId : "";
        if (id === "") return send(res, 400, { error: { code: "bad_request", message: "providerId is required" } });
        speech.prepare(id);
        return send(res, 200, { ok: true });
      }
      send(res, 404, { error: { code: "not_found", message: "No such route" } });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      ctx.logger.warn("nanomuse-listen: settings failed: %s", message);
      send(res, 500, { error: { code: "internal", message } });
    }
  };
  ctx.effect(() => ctx.webServer.register({ kind: "prefix", path: SETTINGS_PREFIX, handler: settings }), "nanomuse-listen: the settings routes");
  void probeCloud();
  const probeTimer = setInterval(() => {
    void probeCloud();
  }, CLOUD_RETRY_MS);
  ctx.effect(() => () => clearInterval(probeTimer), "nanomuse-listen: cloud probe");
}
async function probeCloud() {
  const started = Date.now();
  try {
    const token = await cloudToken();
    if (token === "") throw new Error("\u0410\u043A\u043A\u0430\u0443\u043D\u0442 NanoLika Cloud \u043D\u0435 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0451\u043D");
    const base = process.env.NANOMUSE_CLOUD_URL ?? "https://cloud.nanolika.ru";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4e3);
    try {
      const res = await fetch(`${base.replace(/\/$/, "")}/v1/models`, {
        headers: { authorization: `Bearer ${token}` },
        signal: controller.signal
      });
      if (!res.ok) throw new Error(res.status === 401 ? "\u043A\u043B\u044E\u0447 \u0430\u043A\u043A\u0430\u0443\u043D\u0442\u0430 \u043D\u0435\u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0442\u0435\u043B\u0435\u043D" : `\u043E\u0431\u043B\u0430\u043A\u043E \u043E\u0442\u0432\u0435\u0442\u0438\u043B\u043E ${res.status}`);
    } finally {
      clearTimeout(timer);
    }
    cloudHealth = { ok: true, checkedAt: Date.now(), latencyMs: Date.now() - started, error: "" };
  } catch (error) {
    cloudHealth = {
      ok: false,
      checkedAt: Date.now(),
      latencyMs: Date.now() - started,
      error: error instanceof Error ? error.message : String(error)
    };
  }
  return cloudHealth;
}
async function cloudSpeak(text, model, voice) {
  const token = await cloudToken();
  if (token === "") throw new Error("\u0410\u043A\u043A\u0430\u0443\u043D\u0442 NanoLika Cloud \u043D\u0435 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0451\u043D");
  const base = process.env.NANOMUSE_CLOUD_URL ?? "https://cloud.nanolika.ru";
  const res = await fetch(`${base.replace(/\/$/, "")}/v1/audio/speech`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify({ model, input: text, voice })
  });
  if (!res.ok) {
    const answer = await res.json().catch(() => ({}));
    throw new Error(answer.error?.message ?? `\u043E\u0431\u043B\u0430\u043A\u043E \u043E\u0442\u0432\u0435\u0442\u0438\u043B\u043E ${res.status}`);
  }
  return Buffer.from(await res.arrayBuffer());
}
async function transcribeWithChain(ctx, audio, saved, language, req) {
  const cloudAlive = cloudHealth.ok || Date.now() - cloudHealth.checkedAt > CLOUD_RETRY_MS;
  const wanted = saved.stt === "cloud" && cloudAlive ? ["cloud"] : ["local"];
  const chain = saved.sttFallback ? [...wanted, "local"] : wanted;
  let lastError;
  for (const step of chain) {
    try {
      if (step === "cloud") return { text: await cloudTranscribe(audio, saved.sttModel, language), via: saved.sttModel };
      const speech = ctx.get("speechToText");
      if (speech === void 0) throw new Error("Speech recognition not available");
      const controller = new AbortController();
      req.on("close", () => controller.abort());
      let spec;
      try {
        spec = speech.resolve({ audio, providerId: "whisper-local", language });
      } catch {
        spec = speech.resolve({ audio, language });
      }
      const transcript = await speech.transcribe(spec, controller.signal);
      return { text: transcript.text, via: "whisper-local" };
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError ?? new Error("\u0420\u0430\u0441\u043F\u043E\u0437\u043D\u0430\u0432\u0430\u043D\u0438\u0435 \u043D\u0435 \u043E\u0442\u0432\u0435\u0442\u0438\u043B\u043E");
}
async function cloudToken() {
  try {
    const raw = await readFile(join(home(), ".credentials.yaml"), "utf8");
    const match = /NANOMUSE_CLOUD_TOKEN["']?\s*:\s*["']?([A-Za-z0-9_\-]+)/.exec(raw);
    return match?.[1] ?? "";
  } catch {
    return "";
  }
}
async function cloudTranscribe(audio, model, language) {
  const token = await cloudToken();
  if (token === "") throw new Error("\u0410\u043A\u043A\u0430\u0443\u043D\u0442 NanoLika Cloud \u043D\u0435 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0451\u043D");
  const base = process.env.NANOMUSE_CLOUD_URL ?? "https://cloud.nanolika.ru";
  const form = new FormData();
  form.append("model", model);
  form.append("language", language);
  form.append("file", new Blob([audio], { type: "audio/wav" }), "voice.wav");
  const res = await fetch(`${base.replace(/\/$/, "")}/v1/audio/transcriptions`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}` },
    body: form
  });
  const answer = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(answer.error?.message ?? `\u043E\u0431\u043B\u0430\u043A\u043E \u043E\u0442\u0432\u0435\u0442\u0438\u043B\u043E ${res.status}`);
  return (answer.text ?? "").trim();
}
async function piperReady() {
  try {
    await readFile(join(home(), PIPER_DIR, "ru_RU-irina-medium.onnx"));
    return true;
  } catch {
    return false;
  }
}
async function installedPiper() {
  try {
    const { readdir } = await import("node:fs/promises");
    const entries = await readdir(join(home(), "speech/tts"), { withFileTypes: true });
    return entries.filter((entry) => entry.isDirectory() && entry.name.startsWith("vits-piper-")).map((entry) => entry.name);
  } catch {
    return [];
  }
}
async function installPiper(id) {
  const root = join(home(), "speech/tts");
  const target = join(root, id);
  if ((await installedPiper()).includes(id)) return;
  const { mkdir: mkdir2 } = await import("node:fs/promises");
  await mkdir2(root, { recursive: true });
  const archive = join(tmpdir(), `${id}.tar.bz2`);
  const url = `https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/${id}.tar.bz2`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`\u043D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043A\u0430\u0447\u0430\u0442\u044C \u0433\u043E\u043B\u043E\u0441: ${res.status}`);
  const { writeFile: writeFile2 } = await import("node:fs/promises");
  await writeFile2(archive, Buffer.from(await res.arrayBuffer()));
  try {
    await run("tar", ["-xjf", archive, "-C", root]);
  } finally {
    const { rm: rm2 } = await import("node:fs/promises");
    await rm2(archive, { force: true }).catch(() => void 0);
  }
  const { stat } = await import("node:fs/promises");
  await stat(join(target, "tokens.txt"));
}
var piperServer = null;
function piperRunner() {
  if (piperServer !== null) return piperServer;
  const bin = process.argv[1] ?? "";
  const modules = join(dirname(bin), "..", "..", "..");
  const sherpaMain = join(modules, "sherpa-onnx-node", "sherpa-onnx.js");
  const script = new URL("../tts-piper-server.mjs", import.meta.url).pathname;
  const child = spawn("node", [script, sherpaMain], { stdio: ["pipe", "pipe", "pipe"] });
  const pending = /* @__PURE__ */ new Map();
  let buffer = "";
  child.stdout?.setEncoding("utf8");
  child.stdout?.on("data", (chunk) => {
    buffer += chunk;
    let index = buffer.indexOf("\n");
    while (index >= 0) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      index = buffer.indexOf("\n");
      if (line === "") continue;
      try {
        const answer = JSON.parse(line);
        if (typeof answer.id !== "number") continue;
        const waiter = pending.get(answer.id);
        if (waiter === void 0) continue;
        pending.delete(answer.id);
        if (answer.file !== void 0) waiter.resolve(answer.file);
        else waiter.reject(new Error(answer.error ?? "piper failed"));
      } catch {
      }
    }
  });
  const fail = () => {
    for (const waiter of pending.values()) waiter.reject(new Error("\u0441\u0438\u043D\u0442\u0435\u0437 Piper \u043E\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u043B\u0441\u044F"));
    pending.clear();
    piperServer = null;
  };
  child.on("exit", fail);
  child.on("error", fail);
  piperServer = { child, pending, nextId: 1 };
  return piperServer;
}
async function piperWarmup(voice) {
  const dir = join(home(), "speech/tts", voice);
  const { readdir } = await import("node:fs/promises");
  const files = await readdir(dir).catch(() => []);
  const model = files.find((file) => file.endsWith(".onnx"));
  if (model === void 0) return;
  const runner = piperRunner();
  const id = runner.nextId++;
  await new Promise((resolve) => {
    runner.pending.set(id, { resolve: () => resolve(), reject: () => resolve() });
    runner.child.stdin?.write(`${JSON.stringify({
      id,
      warmup: true,
      model: join(dir, model),
      tokens: join(dir, "tokens.txt"),
      dataDir: join(dir, "espeak-ng-data")
    })}
`);
  });
}
async function piperSpeak(text, voice = "vits-piper-ru_RU-irina-medium") {
  const dir = join(home(), "speech/tts", voice);
  const { readdir } = await import("node:fs/promises");
  const files = await readdir(dir);
  const model = files.find((file2) => file2.endsWith(".onnx"));
  if (model === void 0) throw new Error(`\u0432 \u0433\u043E\u043B\u043E\u0441\u0435 ${voice} \u043D\u0435\u0442 \u043C\u043E\u0434\u0435\u043B\u0438 .onnx`);
  const runner = piperRunner();
  const id = runner.nextId++;
  const file = await new Promise((resolve, reject) => {
    runner.pending.set(id, { resolve, reject });
    runner.child.stdin?.write(`${JSON.stringify({
      id,
      text,
      model: join(dir, model),
      tokens: join(dir, "tokens.txt"),
      dataDir: join(dir, "espeak-ng-data")
    })}
`);
  });
  try {
    return await readFile(file);
  } finally {
    await rm(file, { force: true }).catch(() => void 0);
  }
}
async function listVoices() {
  const output = await capture("say", ["-v", "?"]);
  const voices = [];
  for (const line of output.split("\n")) {
    const match = /^(\S+)\s+([a-z]{2}_[A-Z]{2})\s+#/.exec(line.trim());
    if (match === null) continue;
    voices.push({ name: match[1], lang: match[2] });
    if (voices.length >= VOICE_LIMIT) break;
  }
  return voices;
}
function capture(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr?.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.once("error", reject);
    child.once("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(stderr.trim() || `${command} exited with ${code ?? "no code"}`));
    });
  });
}
function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["ignore", "ignore", "pipe"] });
    let stderr = "";
    child.stderr?.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.once("error", reject);
    child.once("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(stderr.trim() || `${command} exited with ${code ?? "no code"}`));
    });
  });
}
async function readJson(req, limit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    const buffer = chunk;
    size += buffer.length;
    if (size > limit) throw new Error("Request body too large");
    chunks.push(buffer);
  }
  if (chunks.length === 0) return {};
  const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  return parsed !== null && typeof parsed === "object" ? parsed : {};
}
function send(res, status, body) {
  const json = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(json);
}
export {
  apply,
  inject,
  name
};
//# sourceMappingURL=index.js.map
