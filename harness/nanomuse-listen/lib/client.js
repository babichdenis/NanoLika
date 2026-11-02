window.__ModuleLoader__.load({ id: "nanomuse-listen", factory: (require) => {
var module = { exports: {} }; var exports = module.exports;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name2 in all)
    __defProp(target, name2, { get: all[name2], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/client.ts
var client_exports = {};
__export(client_exports, {
  apply: () => apply,
  inject: () => inject,
  name: () => name
});
module.exports = __toCommonJS(client_exports);
var import_react = require("react");
var name = "nanomuse-listen";
var inject = ["slots"];
var RATE = 16e3;
var ENDPOINT = "/nanomuse/voice";
var VOICES = ["Milena", "Samantha", "Zarvox", "Bubbles", "Whisper", "Bells"];
var CLOUD_STT = [
  { id: "ear-ru", name: "GigaAM v3 (\u0440\u0443\u0441\u0441\u043A\u0438\u0439)" },
  { id: "ear-turbo", name: "Whisper Large v3 Turbo" },
  { id: "ear", name: "Whisper 1" },
  { id: "bh-assembly-ai-best", name: "Assembly AI Best" },
  { id: "bh-tbank-voicekit-stt", name: "Voicekit STT (\u0422-\u0411\u0430\u043D\u043A)" }
];
var settingsState = { voice: VOICES[0], answers: false, tts: "system", ttsModel: "voice", cloudVoice: "alloy", piperVoice: "vits-piper-ru_RU-irina-medium", stt: "local", sttModel: "ear-ru", sttFallback: true };
var settingsReady = false;
var settingsSubs = /* @__PURE__ */ new Set();
function emitSettings() {
  for (const sub of settingsSubs) sub();
}
async function ensureSettings() {
  if (settingsReady) return;
  settingsReady = true;
  try {
    const res = await fetch("/nanomuse/listen/settings");
    if (res.ok) {
      const saved = await res.json();
      settingsState = {
        voice: typeof saved.voice === "string" && saved.voice !== "" ? saved.voice : settingsState.voice,
        answers: saved.answers === true,
        tts: saved.tts === "piper" || saved.tts === "cloud" ? saved.tts : "system",
        ttsModel: typeof saved.ttsModel === "string" && saved.ttsModel !== "" ? saved.ttsModel : settingsState.ttsModel,
        cloudVoice: typeof saved.cloudVoice === "string" && saved.cloudVoice !== "" ? saved.cloudVoice : settingsState.cloudVoice,
        piperVoice: typeof saved.piperVoice === "string" && saved.piperVoice !== "" ? saved.piperVoice : settingsState.piperVoice,
        stt: saved.stt === "cloud" ? "cloud" : "local",
        sttModel: typeof saved.sttModel === "string" && saved.sttModel !== "" ? saved.sttModel : settingsState.sttModel,
        sttFallback: saved.sttFallback !== false
      };
      if (settingsState.answers) {
        settingsState = { ...settingsState, answers: false };
        void fetch("/nanomuse/listen/settings", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ answers: false })
        }).catch(() => void 0);
      }
      emitSettings();
      if (settingsState.tts === "piper") warmup();
    }
  } catch {
  }
}
function patchSettings(patch) {
  settingsState = { ...settingsState, ...patch };
  emitSettings();
  void fetch("/nanomuse/listen/settings", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch)
  }).catch(() => void 0);
}
function useSettings() {
  const [, force] = (0, import_react.useState)(0);
  (0, import_react.useEffect)(() => {
    const sub = () => force((tick) => tick + 1);
    settingsSubs.add(sub);
    void ensureSettings();
    return () => {
      settingsSubs.delete(sub);
    };
  }, []);
  return settingsState;
}
var WORDS = {
  ru: { start: "\u041F\u043E\u0441\u0442\u043E\u044F\u043D\u043D\u043E\u0435 \u0441\u043B\u0443\u0448\u0430\u043D\u0438\u0435", stop: "\u041E\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C \u043F\u043E\u0441\u0442\u043E\u044F\u043D\u043D\u043E\u0435 \u0441\u043B\u0443\u0448\u0430\u043D\u0438\u0435" },
  en: { start: "Continuous listening", stop: "Stop continuous listening" },
  zh: { start: "\u6301\u7EED\u8046\u542C", stop: "\u505C\u6B62\u6301\u7EED\u8046\u542C" }
};
function words() {
  const lang = (document.documentElement.lang || navigator.language || "en").slice(0, 2);
  return lang === "ru" ? WORDS.ru : lang === "zh" ? WORDS.zh : WORDS.en;
}
function encodeWave(samples) {
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  const tag = (at, text) => {
    for (let i = 0; i < 4; i += 1) view.setUint8(at + i, text.charCodeAt(i));
  };
  tag(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  tag(8, "WAVE");
  tag(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, RATE, true);
  view.setUint32(28, RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  tag(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 32768 : clamped * 32767, true);
  }
  return bytes;
}
function resample(samples, from) {
  if (from === RATE) return samples;
  const length = Math.max(1, Math.round(samples.length * RATE / from));
  const out = new Float32Array(length);
  const step = from / RATE;
  for (let i = 0; i < length; i += 1) {
    const at = i * step;
    const left = Math.floor(at);
    const right = Math.min(samples.length - 1, left + 1);
    const weight = at - left;
    out[i] = (samples[left] ?? 0) * (1 - weight) + (samples[right] ?? 0) * weight;
  }
  return out;
}
function toBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}
function makeSplitter(onUtterance, settings) {
  const parts = [];
  let heard = 0;
  let silence = 0;
  let voicing = false;
  let rate = 0;
  let floor = 6e-3;
  let muted = false;
  const collect = () => {
    const total = parts.reduce((sum, part) => sum + part.length, 0);
    const all = new Float32Array(total);
    let at = 0;
    for (const part of parts) {
      all.set(part, at);
      at += part.length;
    }
    return all;
  };
  const reset = () => {
    parts.length = 0;
    heard = 0;
    silence = 0;
    voicing = false;
  };
  return {
    /** Deaf for a while: the answer is playing, and a microphone that hears it answers itself. */
    mute(on) {
      muted = on;
      if (on) reset();
    },
    push(samples, sampleRate) {
      if (muted) return;
      rate = sampleRate;
      let sum = 0;
      for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
      const rms = Math.sqrt(sum / samples.length);
      if (!voicing) floor = floor * 0.97 + rms * 0.03;
      const voice = rms > Math.max(0.012, floor * 3);
      if (voice) {
        if (!voicing) settings.onVoiceStart?.();
        voicing = true;
        silence = 0;
        parts.push(Float32Array.from(samples));
        heard += samples.length;
        if (heard / sampleRate * 1e3 >= (settings.maxMs ?? 3e4)) {
          onUtterance(collect());
          reset();
        }
        return;
      }
      if (!voicing) return;
      silence += samples.length / sampleRate * 1e3;
      if (silence < settings.silenceMs) return;
      if (heard / rate * 1e3 >= settings.minMs) onUtterance(collect());
      reset();
    },
    flush() {
      if (voicing && heard > 0) onUtterance(collect());
      reset();
    }
  };
}
function VoiceListen(props) {
  const settings = useSettings();
  const [state, setState] = (0, import_react.useState)("idle");
  const [failure, setFailure] = (0, import_react.useState)("");
  const [analysis, setAnalysis] = (0, import_react.useState)("");
  const [voiceTick, setVoiceTick] = (0, import_react.useState)(0);
  const run = (0, import_react.useRef)(null);
  const text = words();
  const deliver = (phrase) => {
    const actions = props.inputActions;
    if (actions === void 0) {
      console.warn("nanomuse-listen: the composer did not hand inputActions; phrase not sent:", phrase);
      return;
    }
    actions.setDraft(phrase);
    setTimeout(() => actions.submit(), 80);
  };
  globalThis.__listenDeliver = deliver;
  let holdUntil = 0;
  const send = (samples, rate) => {
    const wave = encodeWave(resample(samples, rate));
    const seconds = samples.length / rate;
    let sum = 0;
    for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / Math.max(1, samples.length));
    setState("working");
    void fetch(ENDPOINT, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ audio: toBase64(wave), language: "ru" })
    }).then(async (res) => {
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`);
      const phrase = (answer.text ?? "").trim();
      if (phrase !== "") {
        deliver(phrase);
        const words2 = phrase.split(/\s+/).filter(Boolean).length;
        const wpm = seconds > 0.5 ? Math.round(words2 / (seconds / 60)) : 0;
        const loud = rms > 0.06 ? "\u0433\u0440\u043E\u043C\u043A\u043E" : rms > 0.025 ? "\u043E\u0431\u044B\u0447\u043D\u043E" : "\u0442\u0438\u0445\u043E";
        setAnalysis(`${seconds.toFixed(1)} \u0441 \xB7 ${wpm} \u0441\u043B\u043E\u0432/\u043C\u0438\u043D \xB7 ${loud}`);
      }
      setFailure("");
      if (run.current !== null) setState("listening");
    }).catch((error) => {
      setFailure(error instanceof Error ? error.message : "\u0420\u0430\u0441\u043F\u043E\u0437\u043D\u0430\u0432\u0430\u043D\u0438\u0435 \u043D\u0435 \u043E\u0442\u0432\u0435\u0447\u0430\u0435\u0442");
      if (run.current !== null) setState("listening");
    });
  };
  const start = async () => {
    try {
      patchSettings({ answers: true });
      warmup();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
      });
      const context = new AudioContext();
      if (context.state !== "running") await context.resume().catch(() => void 0);
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const splitter = makeSplitter((samples) => send(samples, context.sampleRate), {
        silenceMs: 900,
        minMs: 350,
        onVoiceStart: () => {
          if (onAir !== null) stopSpeaking();
        }
      });
      let hostSpeaking = false;
      const askHost = setInterval(() => {
        void fetch("/nanomuse/cloud/speaking").then(async (res) => {
          if (!res.ok) return;
          const answer = await res.json().catch(() => ({}));
          hostSpeaking = answer.speaking === true;
        }).catch(() => void 0);
      }, 700);
      processor.onaudioprocess = (event) => {
        splitter.mute(onAir !== null || hostSpeaking);
        splitter.push(event.inputBuffer.getChannelData(0), context.sampleRate);
      };
      source.connect(processor);
      processor.connect(context.destination);
      let watchdog;
      const finish = () => {
        patchSettings({ answers: false });
        if (watchdog !== void 0) clearInterval(watchdog);
        clearInterval(askHost);
        processor.disconnect();
        source.disconnect();
        splitter.flush();
        stream.getTracks().forEach((track2) => track2.stop());
        void context.close().catch(() => void 0);
        run.current = null;
        setState("idle");
        setFailure("");
      };
      const track = stream.getAudioTracks()[0];
      watchdog = setInterval(() => {
        if (context.state !== "running") void context.resume().catch(() => void 0);
        if (track !== void 0 && track.readyState === "ended") finish();
      }, 1500);
      run.current = { stop: finish };
      setState("listening");
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "\u041C\u0438\u043A\u0440\u043E\u0444\u043E\u043D \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u0435\u043D");
      setState("idle");
    }
  };
  const active = state !== "idle";
  return (0, import_react.createElement)("button", {
    type: "button",
    className: `nm-listen${active ? " nm-listen-live" : ""}${failure !== "" ? " nm-listen-failed" : ""}`,
    "aria-label": active ? text.stop : text.start,
    title: failure !== "" ? failure : active ? text.stop : text.start,
    "aria-pressed": active,
    onMouseDown: (event) => event.preventDefault(),
    onContextMenu: (event) => {
      event.preventDefault();
      const now = settings.voice;
      const next = VOICES[(VOICES.indexOf(now) + 1) % VOICES.length];
      patchSettings({ voice: next });
      setAnalysis("");
      setVoiceTick((tick) => tick + 1);
    },
    onClick: () => {
      if (state !== "idle") run.current?.stop();
      else void start();
    }
  }, (0, import_react.createElement)(
    "svg",
    { width: 17, height: 17, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.75, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true },
    (0, import_react.createElement)("path", { d: "M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4" })
  ));
}
var STYLE = `
.nm-listen { width: 34px; height: 34px; border: 0; padding: 0; border-radius: 50%; background: transparent; color: var(--dsw-alias-label-secondary); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.nm-listen:hover { background: var(--nm-hover, rgba(0,0,0,0.06)); color: var(--dsw-alias-label-primary); }
.nm-listen.nm-listen-live { color: #fff; background: #0f9d8f; box-shadow: 0 0 0 4px rgba(15,157,143,0.25); animation: nm-listen-pulse 4.8s ease-in-out infinite; }
.nm-listen.nm-listen-failed { color: #fff; background: #b3261e; }
.nm-speak { display: none !important; width: 34px; height: 34px; border: 0; padding: 0; border-radius: 50%; background: transparent; color: var(--dsw-alias-label-secondary); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.nm-speak:hover { background: var(--nm-hover, rgba(0,0,0,0.06)); color: var(--dsw-alias-label-primary); }
.nm-speak.nm-speak-on { color: #fff; background: #0f9d8f; }
html[data-nanomuse] [data-slot="conversation.input.right"] > .nm-listen { order: 1; }
html[data-nanomuse] [data-slot="conversation.input.right"] > .nm-speak { order: 2; }
@keyframes nm-listen-pulse { 0%, 100% { opacity: 0.72; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .nm-listen.nm-listen-live { animation: none; opacity: 1; } }
.nml-settings { display: flex; flex-direction: column; gap: 12px; max-width: 640px; }
.nml-head { margin: 10px 0 0; font-size: 12px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; opacity: 0.6; }
.nml-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 32px; }
.nml-title { font-weight: 600; }
.nml-hint { margin: 0; opacity: 0.72; font-size: 13px; line-height: 1.45; }
.nml-heard { margin: 0; padding: 10px 12px; border-radius: 8px; background: var(--nm-hover, rgba(0,0,0,0.05)); }
.nml-select { appearance: none; -webkit-appearance: none; min-width: 220px; padding: 8px 34px 8px 12px; border-radius: 10px; border: 1px solid var(--dsw-alias-border-l2, rgba(0,0,0,0.18)); background-color: var(--dsw-alias-bg-layer-1, #fff); background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%237a7a7a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 12px center; color: inherit; font: inherit; font-size: 13px; cursor: pointer; }
.nml-select:hover { border-color: var(--dsw-alias-border-l3, rgba(0,0,0,0.3)); }
.nml-select:focus { outline: 2px solid var(--nm-blue, #0064d4); outline-offset: 1px; }
.nml-meter { height: 6px; border-radius: 999px; background: var(--dsw-alias-bg-layer-3, rgba(0,0,0,0.08)); overflow: hidden; margin: -4px 0 4px; }
.nml-meter-fill { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #2f9e5f, #e0a800 70%, #d2453d); transition: width 90ms linear; }
.nml-btn { padding: 6px 12px; border-radius: 999px; border: 1px solid var(--dsw-alias-border, rgba(0,0,0,0.2)); background: transparent; color: inherit; cursor: pointer; }
.nml-btn:hover { background: var(--nm-hover, rgba(0,0,0,0.06)); }
.nml-btn:disabled { opacity: 0.5; cursor: default; }
.nml-actions { display: inline-flex; gap: 8px; }
.nml-btn-stop { border-color: #b3261e; color: #b3261e; }
.nm-chat-cloud-ctl { position: relative; display: inline-flex; }
.nm-cloud-btn { color: var(--dsw-alias-label-secondary); }
.nm-cloud-btn.nm-cloud-on { color: var(--nm-blue, #0064d4); }
.nm-cloud-menu { position: absolute; z-index: 20; top: calc(100% + 6px); right: 0; min-width: 230px; padding: 8px; border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); box-shadow: 0 10px 30px rgba(0,0,0,0.18); border: 1px solid var(--dsw-alias-border-l2, rgba(0,0,0,0.12)); display: flex; flex-direction: column; gap: 4px; }
.nm-cloud-item { display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 10px; border: 0; border-radius: 8px; background: transparent; color: inherit; font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.nm-cloud-item:hover:not(:disabled) { background: var(--nm-hover, rgba(0,0,0,0.06)); }
.nm-cloud-item:disabled { opacity: 0.45; cursor: default; }
.nm-cloud-note { padding: 4px 10px 2px; font-size: 12px; opacity: 0.7; }
`;
var styled = false;
function ensureStyle() {
  if (styled) return;
  styled = true;
  const style = document.createElement("style");
  style.id = "nanomuse-listen-style";
  style.textContent = STYLE;
  document.head.append(style);
}
function warmup() {
  void fetch("/nanomuse/listen/warmup", { method: "POST" }).catch(() => void 0);
}
var openSection = null;
function openCloudSettings() {
  openSection?.("nanomuse-cloud");
}
function SettingsBridge(props) {
  (0, import_react.useEffect)(() => {
    if (typeof props.openSection === "function") openSection = props.openSection;
    return () => {
      openSection = null;
    };
  }, [props.openSection]);
  return null;
}
function dictationHint() {
  const platform = navigator.platform.toLowerCase();
  if (platform.includes("mac")) return "\u041D\u0430\u0436\u043C\u0438\u0442\u0435 \u043A\u043B\u0430\u0432\u0438\u0448\u0443 \u0434\u0438\u043A\u0442\u043E\u0432\u043A\u0438 (\u0438\u043B\u0438 Fn \u0434\u0432\u0430\u0436\u0434\u044B), \u043A\u043E\u0433\u0434\u0430 \u043A\u0443\u0440\u0441\u043E\u0440 \u0441\u0442\u043E\u0438\u0442 \u0432 \u043F\u043E\u043B\u0435 \u0432\u0432\u043E\u0434\u0430: \u0441\u0438\u0441\u0442\u0435\u043C\u0430 \u043D\u0430\u043F\u0435\u0447\u0430\u0442\u0430\u0435\u0442 \u0441\u043A\u0430\u0437\u0430\u043D\u043D\u043E\u0435.";
  if (platform.includes("win")) return "\u041D\u0430\u0436\u043C\u0438\u0442\u0435 Win+H, \u043A\u043E\u0433\u0434\u0430 \u043A\u0443\u0440\u0441\u043E\u0440 \u0441\u0442\u043E\u0438\u0442 \u0432 \u043F\u043E\u043B\u0435 \u0432\u0432\u043E\u0434\u0430: Windows \u043D\u0430\u043F\u0435\u0447\u0430\u0442\u0430\u0435\u0442 \u0441\u043A\u0430\u0437\u0430\u043D\u043D\u043E\u0435.";
  return "\u0412\u043A\u043B\u044E\u0447\u0438\u0442\u0435 \u0434\u0438\u043A\u0442\u043E\u0432\u043A\u0443 \u0441\u0432\u043E\u0435\u0439 \u0441\u0438\u0441\u0442\u0435\u043C\u044B, \u043A\u043E\u0433\u0434\u0430 \u043A\u0443\u0440\u0441\u043E\u0440 \u0441\u0442\u043E\u0438\u0442 \u0432 \u043F\u043E\u043B\u0435 \u0432\u0432\u043E\u0434\u0430.";
}
async function recordTake(seconds, onLevel) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const context = new AudioContext();
  if (context.state !== "running") await context.resume().catch(() => void 0);
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const parts = [];
  processor.onaudioprocess = (event) => {
    const chunk = event.inputBuffer.getChannelData(0);
    parts.push(Float32Array.from(chunk));
    if (onLevel !== void 0) {
      let sum = 0;
      for (let i = 0; i < chunk.length; i += 1) sum += chunk[i] * chunk[i];
      onLevel(Math.min(1, Math.sqrt(sum / chunk.length) * 6));
    }
  };
  source.connect(processor);
  processor.connect(context.destination);
  await new Promise((resolve) => setTimeout(resolve, seconds * 1e3));
  processor.disconnect();
  source.disconnect();
  stream.getTracks().forEach((track) => track.stop());
  const rate = context.sampleRate;
  void context.close().catch(() => void 0);
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const samples = new Float32Array(total);
  let at = 0;
  for (const part of parts) {
    samples.set(part, at);
    at += part.length;
  }
  return { samples, rate };
}
function playAudio(audio) {
  return new Promise((resolve) => {
    const track = new Audio(`data:audio/wav;base64,${audio}`);
    track.onended = () => resolve();
    track.onerror = () => resolve();
    track.play().catch(() => resolve());
  });
}
function DictationSettings() {
  const settings = useSettings();
  const [voices, setVoices] = (0, import_react.useState)([]);
  const [status, setStatus] = (0, import_react.useState)("");
  const [heard, setHeard] = (0, import_react.useState)("");
  const [busy, setBusy] = (0, import_react.useState)(false);
  const [playing, setPlaying] = (0, import_react.useState)(false);
  const [level, setLevel] = (0, import_react.useState)(0);
  const [stt, setStt] = (0, import_react.useState)(null);
  const [ttsProviders, setTtsProviders] = (0, import_react.useState)([]);
  const [health, setHealth] = (0, import_react.useState)(null);
  const [piperVoices, setPiperVoices] = (0, import_react.useState)([]);
  const loadStt = () => {
    void fetch("/nanomuse/listen/stt").then(async (res) => res.ok ? await res.json() : null).then((snapshot) => {
      if (snapshot !== null) setStt(snapshot);
    }).catch(() => void 0);
  };
  (0, import_react.useEffect)(loadStt, []);
  (0, import_react.useEffect)(() => {
    const load = () => {
      void fetch("/nanomuse/listen/health").then(async (res) => res.ok ? await res.json() : null).then((answer) => {
        if (answer !== null) setHealth(answer);
      }).catch(() => void 0);
    };
    load();
    const timer = setInterval(load, 3e4);
    return () => clearInterval(timer);
  }, []);
  const loadPiper = () => {
    void fetch("/nanomuse/listen/tts/voices").then(async (res) => res.ok ? await res.json() : {}).then((answer) => setPiperVoices(Array.isArray(answer.voices) ? answer.voices : [])).catch(() => void 0);
  };
  (0, import_react.useEffect)(loadPiper, []);
  const installPiper = async (id) => {
    setBusy(true);
    setStatus("\u0421\u043A\u0430\u0447\u0438\u0432\u0430\u044E \u0433\u043E\u043B\u043E\u0441\u2026");
    try {
      const res = await fetch("/nanomuse/listen/tts/install", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id })
      });
      if (!res.ok) {
        const answer = await res.json().catch(() => ({}));
        throw new Error(answer.error?.message ?? `${res.status}`);
      }
      loadPiper();
      patchSettings({ piperVoice: id });
      setStatus("\u0413\u043E\u043B\u043E\u0441 \u0441\u043A\u0430\u0447\u0430\u043D");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "\u041D\u0435 \u043F\u043E\u043B\u0443\u0447\u0438\u043B\u043E\u0441\u044C");
    } finally {
      setBusy(false);
    }
  };
  (0, import_react.useEffect)(() => {
    void fetch("/nanomuse/listen/tts").then(async (res) => res.ok ? await res.json() : {}).then((answer) => setTtsProviders(Array.isArray(answer.providers) ? answer.providers : [])).catch(() => void 0);
  }, []);
  const chooseStt = async (providerId) => {
    setBusy(true);
    try {
      const res = await fetch("/nanomuse/listen/stt", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ providerId })
      });
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`);
      if (Array.isArray(answer.providers)) setStt(answer);
      setStatus("\u0420\u0430\u0441\u043F\u043E\u0437\u043D\u0430\u0432\u0430\u043D\u0438\u0435 \u043F\u0435\u0440\u0435\u043A\u043B\u044E\u0447\u0435\u043D\u043E");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "\u041D\u0435 \u043F\u043E\u043B\u0443\u0447\u0438\u043B\u043E\u0441\u044C");
    } finally {
      setBusy(false);
    }
  };
  const chooseLanguage = async (language) => {
    setBusy(true);
    try {
      const res = await fetch("/nanomuse/listen/stt", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ language })
      });
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`);
      if (Array.isArray(answer.providers)) setStt(answer);
    } catch {
    } finally {
      setBusy(false);
    }
  };
  const prepareStt = async (providerId) => {
    setBusy(true);
    try {
      const res = await fetch("/nanomuse/listen/stt/prepare", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ providerId })
      });
      if (!res.ok) {
        const answer = await res.json().catch(() => ({}));
        throw new Error(answer.error?.message ?? `${res.status}`);
      }
      setStatus("\u041C\u043E\u0434\u0435\u043B\u044C \u0433\u043E\u0442\u043E\u0432\u0438\u0442\u0441\u044F\u2026");
      setTimeout(loadStt, 3e3);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "\u041D\u0435 \u043F\u043E\u043B\u0443\u0447\u0438\u043B\u043E\u0441\u044C");
    } finally {
      setBusy(false);
    }
  };
  const testVoice = async () => {
    setBusy(true);
    setStatus("\u0413\u043E\u0442\u043E\u0432\u043B\u044E \u0433\u043E\u043B\u043E\u0441\u2026");
    try {
      const res = await fetch("/nanomuse/speak", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: "\u041F\u0440\u0438\u0432\u0435\u0442, \u0414\u0435\u043D\u0438\u0441. \u0422\u0430\u043A \u0437\u0432\u0443\u0447\u0438\u0442 \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u0439 \u0433\u043E\u043B\u043E\u0441. \u0415\u0441\u043B\u0438 \u0441\u043B\u044B\u0448\u043D\u043E \u2014 \u0432\u0441\u0451 \u043D\u0430\u0441\u0442\u0440\u043E\u0435\u043D\u043E.", voice: settings.voice, provider: settings.tts })
      });
      const answer = await res.json();
      if (!res.ok || answer.audio === void 0) throw new Error(answer.error?.message ?? `${res.status}`);
      setStatus("\u0418\u0433\u0440\u0430\u044E\u2026");
      setPlaying(true);
      await playAudio(answer.audio);
      setStatus("\u0413\u043E\u043B\u043E\u0441 \u043F\u0440\u043E\u0432\u0435\u0440\u0435\u043D");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "\u041D\u0435 \u043F\u043E\u043B\u0443\u0447\u0438\u043B\u043E\u0441\u044C");
    } finally {
      setPlaying(false);
      setBusy(false);
    }
  };
  const testMic = async () => {
    setBusy(true);
    setHeard("");
    setStatus("\u0413\u043E\u0432\u043E\u0440\u0438 3 \u0441\u0435\u043A\u0443\u043D\u0434\u044B\u2026");
    try {
      const { samples, rate } = await recordTake(3, (value) => setLevel(value));
      setLevel(0);
      setStatus("\u0420\u0430\u0441\u043F\u043E\u0437\u043D\u0430\u044E\u2026");
      const wave = encodeWave(resample(samples, rate));
      const res = await fetch(ENDPOINT, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ audio: toBase64(wave), language: "ru" })
      });
      const answer = await res.json();
      if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`);
      const phrase = (answer.text ?? "").trim();
      setHeard(phrase === "" ? "(\u0442\u0438\u0448\u0438\u043D\u0430, \u0440\u0435\u0447\u044C \u043D\u0435 \u0440\u0430\u0437\u043E\u0431\u0440\u0430\u043D\u0430)" : phrase);
      setStatus("\u041C\u0438\u043A\u0440\u043E\u0444\u043E\u043D \u043F\u0440\u043E\u0432\u0435\u0440\u0435\u043D");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "\u041D\u0435 \u043F\u043E\u043B\u0443\u0447\u0438\u043B\u043E\u0441\u044C");
    } finally {
      setBusy(false);
    }
  };
  const listed = voices.length > 0 ? voices.map((voice) => voice.name) : VOICES;
  const voiceOptions = listed.includes(settings.voice) ? listed : [settings.voice, ...listed];
  const provider = stt?.providers?.find((item) => item.id === stt.selection?.providerId);
  const languages = provider?.languages ?? ["ru"];
  const phase = provider?.preparation?.phase ?? "unprepared";
  const phaseWord = phase === "ready" ? "\u0433\u043E\u0442\u043E\u0432\u0430" : phase === "downloading" ? "\u0441\u043A\u0430\u0447\u0438\u0432\u0430\u0435\u0442\u0441\u044F" : phase === "loading" ? "\u0437\u0430\u0433\u0440\u0443\u0436\u0430\u0435\u0442\u0441\u044F" : phase === "failed" ? "\u043E\u0448\u0438\u0431\u043A\u0430" : "\u043D\u0435 \u043F\u043E\u0434\u0433\u043E\u0442\u043E\u0432\u043B\u0435\u043D\u0430";
  const ttsWord = settings.tts === "system" ? "\u0421\u0438\u0441\u0442\u0435\u043C\u043D\u044B\u0439 \u0433\u043E\u043B\u043E\u0441" : settings.tts === "silero" ? "Silero (\u043B\u043E\u043A\u0430\u043B\u044C\u043D\u043E, \u043D\u0435 \u0443\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D)" : "\u041E\u0431\u043B\u0430\u043A\u043E (\u043D\u0435 \u043D\u0430\u0441\u0442\u0440\u043E\u0435\u043D\u043E)";
  return (0, import_react.createElement)(
    "div",
    { className: "nml-settings" },
    (0, import_react.createElement)("p", { className: "nml-hint" }, "\u0414\u0435\u0442\u0435\u043A\u0446\u0438\u044F \u0440\u0435\u0447\u0438 \u0438 \u0433\u043E\u043B\u043E\u0441 \u041B\u0438\u043A\u0438. \u0412\u0441\u0451 \u0440\u0430\u0431\u043E\u0442\u0430\u0435\u0442 \u043D\u0430 \u044D\u0442\u043E\u043C \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435: \u0440\u0430\u0441\u043F\u043E\u0437\u043D\u0430\u0451\u0442 \u043B\u043E\u043A\u0430\u043B\u044C\u043D\u0430\u044F \u043D\u0435\u0439\u0440\u043E\u0441\u0435\u0442\u044C, \u0433\u043E\u0432\u043E\u0440\u0438\u0442 \u0441\u0438\u0441\u0442\u0435\u043C\u043D\u044B\u0439 \u0433\u043E\u043B\u043E\u0441."),
    (0, import_react.createElement)("h3", { className: "nml-head" }, "\u0414\u0435\u0442\u0435\u043A\u0446\u0438\u044F \u0440\u0435\u0447\u0438"),
    (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u0420\u0430\u0441\u043F\u043E\u0437\u043D\u0430\u0432\u0430\u0442\u0435\u043B\u044C"),
      (0, import_react.createElement)("select", {
        className: "nml-select",
        value: settings.stt === "cloud" ? "cloud" : stt?.selection?.providerId ?? "",
        onChange: (event) => {
          const value = event.target.value;
          if (value === "cloud") patchSettings({ stt: "cloud" });
          else {
            patchSettings({ stt: "local" });
            void chooseStt(value);
          }
        }
      }, [
        ...(stt?.providers ?? []).map((item) => (0, import_react.createElement)(
          "option",
          { key: item.id, value: item.id },
          `${item.name}${item.location === "cloud" ? " (\u043E\u0431\u043B\u0430\u043A\u043E)" : " (\u043B\u043E\u043A\u0430\u043B\u044C\u043D\u043E)"}${item.id === "whisper-local" ? " \u2014 \u043F\u043E \u0443\u043C\u043E\u043B\u0447\u0430\u043D\u0438\u044E" : ""}`
        )),
        (0, import_react.createElement)("option", { key: "cloud", value: "cloud" }, "NanoLika Cloud (\u043E\u0431\u043B\u0430\u043A\u043E)")
      ])
    ),
    settings.stt === "cloud" ? (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u041C\u043E\u0434\u0435\u043B\u044C \u043E\u0431\u043B\u0430\u043A\u0430"),
      (0, import_react.createElement)("select", {
        className: "nml-select",
        value: settings.sttModel,
        onChange: (event) => patchSettings({ sttModel: event.target.value })
      }, CLOUD_STT.map((model) => (0, import_react.createElement)("option", { key: model.id, value: model.id }, `${model.name}${model.id === "ear-ru" ? " \u2014 \u043F\u043E \u0443\u043C\u043E\u043B\u0447\u0430\u043D\u0438\u044E" : ""}`)))
    ) : null,
    (0, import_react.createElement)(
      "div",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-hint" }, health === null ? "\u041F\u0440\u043E\u0432\u0435\u0440\u044F\u044E \u043E\u0431\u043B\u0430\u043A\u043E\u2026" : health.cloud.ok ? `\u041E\u0431\u043B\u0430\u043A\u043E \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u043E \xB7 ${health.cloud.latencyMs} \u043C\u0441 \xB7 \u043E\u0442\u0432\u0435\u0447\u0430\u0435\u0442: ${health.stt === "local" ? "\u043B\u043E\u043A\u0430\u043B\u044C\u043D\u043E" : health.stt}` : `\u041E\u0431\u043B\u0430\u043A\u043E \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u043E: ${health.cloud.error}. \u0420\u0430\u0431\u043E\u0442\u0430\u0435\u043C \u043B\u043E\u043A\u0430\u043B\u044C\u043D\u043E, \u043F\u0435\u0440\u0435\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435 \u0430\u0432\u0442\u043E\u043C\u0430\u0442\u0438\u0447\u0435\u0441\u043A\u043E\u0435.`),
      health !== null && !health.cloud.ok ? (0, import_react.createElement)("button", { type: "button", className: "nml-btn", onClick: () => openCloudSettings() }, "\u0412\u043E\u0439\u0442\u0438 \u0437\u0430\u043D\u043E\u0432\u043E") : null
    ),
    settings.stt === "cloud" ? (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u041F\u0440\u0438 \u0441\u0431\u043E\u0435 \u043E\u0431\u043B\u0430\u043A\u0430 \u2014 \u043B\u043E\u043A\u0430\u043B\u044C\u043D\u043E"),
      (0, import_react.createElement)("input", {
        type: "checkbox",
        checked: settings.sttFallback,
        onChange: (event) => patchSettings({ sttFallback: event.target.checked })
      })
    ) : null,
    settings.stt === "cloud" ? null : (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u042F\u0437\u044B\u043A"),
      (0, import_react.createElement)("select", {
        className: "nml-select",
        value: stt?.selection?.language ?? "ru",
        onChange: (event) => void chooseLanguage(event.target.value)
      }, languages.map((code) => (0, import_react.createElement)("option", { key: code, value: code }, code === "auto" ? "\u043E\u043F\u0440\u0435\u0434\u0435\u043B\u044F\u0442\u044C \u0441\u0430\u043C" : code)))
    ),
    (0, import_react.createElement)(
      "div",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-hint" }, `\u041C\u043E\u0434\u0435\u043B\u044C: ${phaseWord}`),
      (0, import_react.createElement)("button", { type: "button", className: "nml-btn", disabled: busy || phase === "ready", onClick: () => provider !== void 0 && void prepareStt(provider.id) }, "\u041F\u043E\u0434\u0433\u043E\u0442\u043E\u0432\u0438\u0442\u044C \u043C\u043E\u0434\u0435\u043B\u044C")
    ),
    (0, import_react.createElement)(
      "div",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-hint" }, "\u0417\u0430\u043F\u0438\u0441\u044C 3 \u0441\u0435\u043A\u0443\u043D\u0434\u044B \u0438 \u0440\u0430\u0441\u043F\u043E\u0437\u043D\u0430\u0432\u0430\u043D\u0438\u0435"),
      (0, import_react.createElement)("button", { type: "button", className: "nml-btn", disabled: busy, onClick: () => void testMic() }, "\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u043C\u0438\u043A\u0440\u043E\u0444\u043E\u043D")
    ),
    (0, import_react.createElement)(
      "div",
      { className: "nml-meter", role: "meter", "aria-label": "\u0423\u0440\u043E\u0432\u0435\u043D\u044C \u043C\u0438\u043A\u0440\u043E\u0444\u043E\u043D\u0430", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(level * 100) },
      (0, import_react.createElement)("span", { className: "nml-meter-fill", style: { width: `${Math.round(level * 100)}%` } })
    ),
    heard !== "" ? (0, import_react.createElement)("p", { className: "nml-heard" }, `\u0423\u0441\u043B\u044B\u0448\u0430\u043B\u0430: ${heard}`) : null,
    (0, import_react.createElement)("h3", { className: "nml-head" }, "\u0413\u0435\u043D\u0435\u0440\u0430\u0446\u0438\u044F \u0433\u043E\u043B\u043E\u0441\u0430"),
    (0, import_react.createElement)("p", { className: "nml-hint" }, "\u0414\u0432\u0438\u0436\u043E\u043A \u2014 \u043A\u0442\u043E \u043E\u0437\u0432\u0443\u0447\u0438\u0432\u0430\u0435\u0442 (\u0441\u0438\u0441\u0442\u0435\u043C\u0430, Piper \u0438\u043B\u0438 \u043E\u0431\u043B\u0430\u043A\u043E). \u0413\u043E\u043B\u043E\u0441 \u2014 \u043A\u0442\u043E \u0438\u043C\u0435\u043D\u043D\u043E \u0433\u043E\u0432\u043E\u0440\u0438\u0442 \u0432\u043D\u0443\u0442\u0440\u0438 \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u043E\u0433\u043E \u0434\u0432\u0438\u0436\u043A\u0430."),
    (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u0414\u0432\u0438\u0436\u043E\u043A \u043E\u0437\u0432\u0443\u0447\u043A\u0438"),
      (0, import_react.createElement)("select", {
        className: "nml-select",
        value: settings.tts,
        onChange: (event) => patchSettings({ tts: event.target.value })
      }, (ttsProviders.length > 0 ? ttsProviders : [
        { id: "system", name: "\u0421\u0438\u0441\u0442\u0435\u043C\u043D\u044B\u0439 \u0433\u043E\u043B\u043E\u0441", ready: true },
        { id: "piper", name: "Piper \u0418\u0440\u0438\u043D\u0430 (\u043D\u0435\u0439\u0440\u043E\u0433\u043E\u043B\u043E\u0441, \u043B\u043E\u043A\u0430\u043B\u044C\u043D\u043E)", ready: true },
        { id: "cloud", name: "NanoLika Cloud (\u043E\u0431\u043B\u0430\u043A\u043E)", ready: true }
      ]).map((item) => (0, import_react.createElement)(
        "option",
        { key: item.id, value: item.id },
        `${item.name}${item.id === "system" ? " \u2014 \u043F\u043E \u0443\u043C\u043E\u043B\u0447\u0430\u043D\u0438\u044E" : ""}`
      )))
    ),
    settings.tts === "system" ? (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u0413\u043E\u043B\u043E\u0441"),
      (0, import_react.createElement)("select", {
        className: "nml-select",
        value: settings.voice,
        onChange: (event) => patchSettings({ voice: event.target.value })
      }, voiceOptions.map((name2) => (0, import_react.createElement)("option", { key: name2, value: name2 }, name2)))
    ) : null,
    settings.tts === "piper" ? (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u0413\u043E\u043B\u043E\u0441"),
      (0, import_react.createElement)("select", {
        className: "nml-select",
        value: settings.piperVoice,
        onChange: (event) => patchSettings({ piperVoice: event.target.value })
      }, (piperVoices.length > 0 ? piperVoices : [{ id: "vits-piper-ru_RU-irina-medium", title: "\u0418\u0440\u0438\u043D\u0430 \u2014 \u0440\u0443\u0441\u0441\u043A\u0438\u0439, \u0436\u0435\u043D\u0441\u043A\u0438\u0439", lang: "ru", installed: true }]).map((voice) => (0, import_react.createElement)("option", { key: voice.id, value: voice.id }, `${voice.title}${voice.installed ? "" : " \u2014 \u043D\u0435 \u0441\u043A\u0430\u0447\u0430\u043D"}`)))
    ) : null,
    settings.tts === "piper" && piperVoices.some((voice) => voice.id === settings.piperVoice && !voice.installed) ? (0, import_react.createElement)(
      "div",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-hint" }, "\u0413\u043E\u043B\u043E\u0441 \u0435\u0449\u0451 \u043D\u0435 \u0441\u043A\u0430\u0447\u0430\u043D \u043D\u0430 \u044D\u0442\u043E\u0442 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440"),
      (0, import_react.createElement)("button", { type: "button", className: "nml-btn", disabled: busy, onClick: () => void installPiper(settings.piperVoice) }, "\u0421\u043A\u0430\u0447\u0430\u0442\u044C \u0433\u043E\u043B\u043E\u0441")
    ) : null,
    settings.tts === "cloud" ? (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u041C\u043E\u0434\u0435\u043B\u044C \u043E\u0431\u043B\u0430\u043A\u0430"),
      (0, import_react.createElement)(
        "select",
        {
          className: "nml-select",
          value: settings.ttsModel,
          onChange: (event) => patchSettings({ ttsModel: event.target.value })
        },
        (0, import_react.createElement)("option", { value: "voice" }, "Gemini Voice \u2014 \u043F\u043E \u0443\u043C\u043E\u043B\u0447\u0430\u043D\u0438\u044E"),
        (0, import_react.createElement)("option", { value: "voice-hd" }, "MiniMax Speech HD"),
        (0, import_react.createElement)("option", { value: "bh-tts-1" }, "TTS 1"),
        (0, import_react.createElement)("option", { value: "bh-tts-1-hd" }, "TTS 1 HD")
      )
    ) : null,
    settings.tts === "cloud" ? (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u0413\u043E\u043B\u043E\u0441 \u043E\u0431\u043B\u0430\u043A\u0430"),
      (0, import_react.createElement)("select", {
        className: "nml-select",
        value: settings.cloudVoice,
        onChange: (event) => patchSettings({ cloudVoice: event.target.value })
      }, ["alloy", "echo", "fable", "onyx", "nova", "shimmer"].map((name2) => (0, import_react.createElement)("option", { key: name2, value: name2 }, name2)))
    ) : null,
    (0, import_react.createElement)(
      "div",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-hint" }, status !== "" ? status : ttsWord),
      (0, import_react.createElement)(
        "span",
        { className: "nml-actions" },
        (0, import_react.createElement)("button", { type: "button", className: "nml-btn", disabled: busy && !playing, onClick: () => void testVoice() }, "\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0433\u043E\u043B\u043E\u0441"),
        playing ? (0, import_react.createElement)("button", { type: "button", className: "nml-btn nml-btn-stop", onClick: () => {
          stopSpeaking();
          setPlaying(false);
          setStatus("\u041E\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D\u043E");
        } }, "\u0421\u0442\u043E\u043F") : null
      )
    ),
    (0, import_react.createElement)(
      "label",
      { className: "nml-row" },
      (0, import_react.createElement)("span", { className: "nml-title" }, "\u041E\u0437\u0432\u0443\u0447\u0438\u0432\u0430\u0442\u044C \u043E\u0442\u0432\u0435\u0442\u044B"),
      (0, import_react.createElement)("input", {
        type: "checkbox",
        checked: settings.answers,
        onChange: (event) => patchSettings({ answers: event.target.checked })
      })
    ),
    (0, import_react.createElement)("h3", { className: "nml-head" }, "\u0421\u0438\u0441\u0442\u0435\u043C\u043D\u0430\u044F \u0434\u0438\u043A\u0442\u043E\u0432\u043A\u0430"),
    (0, import_react.createElement)("p", { className: "nml-hint" }, dictationHint())
  );
}
function ChatCloudButton(props) {
  const sessionId = props.sessionId ?? "";
  const [mark, setMark] = (0, import_react.useState)("unknown");
  const [syncedHere, setSyncedHere] = (0, import_react.useState)(false);
  const [open, setOpen] = (0, import_react.useState)(false);
  const [busy, setBusy] = (0, import_react.useState)(false);
  const [note, setNote] = (0, import_react.useState)("");
  const [channels, setChannels] = (0, import_react.useState)([]);
  (0, import_react.useEffect)(() => {
    if (sessionId === "") return;
    let alive = true;
    const read = () => {
      void fetch("/nanomuse/cloud/sync/state", { headers: { "x-nanomuse": "1" } }).then(async (res) => res.ok ? await res.json() : {}).then((view) => {
        if (!alive) return;
        const sessions = Array.isArray(view.sessions) ? view.sessions : [];
        const synced = Array.isArray(view.synced) ? view.synced : [];
        setSyncedHere(synced.includes(sessionId));
        if (view.mainSession === sessionId) setMark("main");
        else if (sessions.includes(sessionId) || synced.includes(sessionId)) setMark("shared");
        else setMark("local");
      }).catch(() => void 0);
    };
    read();
    const again = () => read();
    window.addEventListener("focus", again);
    window.addEventListener("nanomuse:cloud-changed", again);
    document.addEventListener("visibilitychange", again);
    return () => {
      alive = false;
      window.removeEventListener("focus", again);
      window.removeEventListener("nanomuse:cloud-changed", again);
      document.removeEventListener("visibilitychange", again);
    };
  }, [sessionId]);
  (0, import_react.useEffect)(() => {
    void fetch("/nanomuse/cloud/link/channels", { headers: { "x-nanomuse": "1" } }).then(async (res) => res.ok ? await res.json() : {}).then((answer) => setChannels(Array.isArray(answer.channels) ? answer.channels : [])).catch(() => void 0);
  }, []);
  if (sessionId === "") return null;
  const toggle = async (on) => {
    setBusy(true);
    setNote("");
    try {
      const res = await fetch("/nanomuse/cloud/sync/synced", {
        method: "POST",
        headers: { "content-type": "application/json", "x-nanomuse": "1" },
        body: JSON.stringify({ sessionId, on })
      });
      const answer = await res.json().catch(() => ({}));
      if (!res.ok || answer.ok === false) throw new Error(answer.error?.message ?? answer.reason ?? `${res.status}`);
      setSyncedHere(on);
      window.dispatchEvent(new CustomEvent("nanomuse:cloud-changed"));
      setNote(on ? "\u0427\u0430\u0442 \u0432 \u043E\u0431\u043B\u0430\u043A\u0435" : "\u0422\u043E\u043B\u044C\u043A\u043E \u043D\u0430 \u044D\u0442\u043E\u043C \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435");
    } catch (error) {
      setNote(error instanceof Error ? error.message : "\u041D\u0435 \u043F\u043E\u043B\u0443\u0447\u0438\u043B\u043E\u0441\u044C");
    } finally {
      setBusy(false);
    }
  };
  const openIn = async (channel) => {
    setBusy(true);
    setNote("");
    try {
      const res = await fetch("/nanomuse/cloud/conversation/open", {
        method: "POST",
        headers: { "content-type": "application/json", "x-nanomuse": "1" },
        body: JSON.stringify({ sessionId, channel })
      });
      if (!res.ok) {
        const answer = await res.json().catch(() => ({}));
        throw new Error(answer.error?.message ?? `${res.status}`);
      }
      setNote(channel === "telegram" ? "\u041E\u0442\u043A\u0440\u044B\u043B \u0432 Telegram" : "\u041E\u0442\u043A\u0440\u044B\u043B \u0432 MAX");
    } catch (error) {
      setNote(error instanceof Error ? error.message : "\u041D\u0435 \u043F\u043E\u043B\u0443\u0447\u0438\u043B\u043E\u0441\u044C");
    } finally {
      setBusy(false);
    }
  };
  const hasTelegram = channels.some((item) => item.channel === "telegram");
  const hasMax = channels.some((item) => item.channel === "max");
  const shared = mark === "main" || mark === "shared";
  const title = mark === "unknown" ? "\u041F\u0440\u043E\u0432\u0435\u0440\u044F\u044E \u043E\u0431\u043B\u0430\u043A\u043E\u2026" : mark === "main" ? "\u0412 \u043E\u0431\u043B\u0430\u043A\u0435: \u043E\u0441\u043D\u043E\u0432\u043D\u043E\u0439 \u0447\u0430\u0442 \u0430\u043A\u043A\u0430\u0443\u043D\u0442\u0430" : shared ? "\u0412 \u043E\u0431\u043B\u0430\u043A\u0435: \u0432\u0435\u0431 \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430" : "\u0422\u043E\u043B\u044C\u043A\u043E \u043D\u0430 \u044D\u0442\u043E\u043C \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435";
  return (0, import_react.createElement)(
    "div",
    { className: "nm-chat-cloud-ctl" },
    (0, import_react.createElement)("button", {
      type: "button",
      className: `nm-icon-btn nm-cloud-btn${shared ? " nm-cloud-on" : ""}`,
      "aria-label": title,
      title,
      disabled: busy,
      onClick: () => setOpen((current) => !current)
    }, (0, import_react.createElement)(
      "svg",
      { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true },
      (0, import_react.createElement)("path", { d: "M2.25 15a4.5 4.5 0 0 0 4.5 4.5H18a3.75 3.75 0 0 0 1.332-7.257 3 3 0 0 0-3.758-3.848 5.25 5.25 0 0 0-10.233 2.33A4.502 4.502 0 0 0 2.25 15z" }),
      mark === "local" ? (0, import_react.createElement)("path", { d: "M3.5 3.5l17 17" }) : null
    )),
    open ? (0, import_react.createElement)(
      "div",
      { className: "nm-cloud-menu" },
      (0, import_react.createElement)(
        "label",
        { className: "nm-cloud-item" },
        (0, import_react.createElement)("input", { type: "checkbox", checked: mark === "main" ? true : syncedHere, disabled: busy || mark === "main" || mark === "unknown", onChange: (event) => void toggle(event.target.checked) }),
        (0, import_react.createElement)("span", null, mark === "main" ? "\u041E\u0441\u043D\u043E\u0432\u043D\u043E\u0439 \u0447\u0430\u0442 \u0432\u0441\u0435\u0433\u0434\u0430 \u0432 \u043E\u0431\u043B\u0430\u043A\u0435" : "\u0421\u0438\u043D\u0445\u0440\u043E\u043D\u0438\u0437\u0438\u0440\u043E\u0432\u0430\u0442\u044C \u0441 \u044D\u0442\u0438\u043C \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E\u043C")
      ),
      (0, import_react.createElement)("button", { type: "button", className: "nm-cloud-item", disabled: busy || !hasTelegram, onClick: () => void openIn("telegram") }, "\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0432 Telegram"),
      (0, import_react.createElement)("button", { type: "button", className: "nm-cloud-item", disabled: busy || !hasMax, onClick: () => void openIn("max") }, "\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0432 MAX"),
      note !== "" ? (0, import_react.createElement)("span", { className: "nm-cloud-note" }, note) : null
    ) : null
  );
}
var onAir = null;
var dropQueue = null;
function stopSpeaking() {
  dropQueue?.();
  if (onAir !== null) {
    onAir.pause();
    onAir = null;
  }
}
function VoiceAnswers() {
  const settings = useSettings();
  const on = settings.answers;
  const queue = (0, import_react.useRef)([]);
  const playing = (0, import_react.useRef)(false);
  const seen = (0, import_react.useRef)(/* @__PURE__ */ new WeakSet());
  const timers = (0, import_react.useRef)(/* @__PURE__ */ new Map());
  const spoken = (0, import_react.useRef)(/* @__PURE__ */ new Map());
  const lastChange = (0, import_react.useRef)(/* @__PURE__ */ new Map());
  const ru = (document.documentElement.lang || "").startsWith("ru");
  (0, import_react.useEffect)(() => {
    dropQueue = () => {
      queue.current.length = 0;
      playing.current = false;
    };
    return () => {
      dropQueue = null;
    };
  }, []);
  const playNext = () => {
    if (playing.current) return;
    const next = queue.current.shift();
    if (next === void 0) return;
    playing.current = true;
    void fetch("/nanomuse/speak", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: next, voice: settings.voice, provider: settings.tts })
    }).then(async (res) => {
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`);
      const track = new Audio(`data:audio/wav;base64,${answer.audio ?? ""}`);
      onAir = track;
      track.onended = () => {
        if (onAir === track) onAir = null;
        playing.current = false;
        playNext();
      };
      track.onerror = () => {
        if (onAir === track) onAir = null;
        playing.current = false;
        playNext();
      };
      await track.play();
    }).catch(() => {
      playing.current = false;
      playNext();
    });
  };
  const speak = (text) => {
    const plain = text.trim();
    if (plain === "") return;
    queue.current.push(plain);
    playNext();
  };
  (0, import_react.useEffect)(() => {
    if (!on) return;
    const RESPONSE = "[data-chat-flow-kind=assistant-step][data-chat-group-part=response]";
    const watch = (node, quietly = false) => {
      if (seen.current.has(node)) return;
      seen.current.add(node);
      if (quietly) return;
      const clean = () => (node.textContent ?? "").replace(/\s+/g, " ").trim();
      const pump = () => {
        const text = clean();
        if (text === "") return;
        let done = spoken.current.get(node) ?? "";
        if (!text.startsWith(done)) done = "";
        const fresh = text.slice(done.length);
        const boundary = Math.max(fresh.lastIndexOf(". "), fresh.lastIndexOf("! "), fresh.lastIndexOf("? "), fresh.lastIndexOf("\u2026 "));
        const atEnd = /[.!?…]\s*$/.test(fresh);
        const early = done === "" && fresh.length >= 48;
        const space = early ? Math.max(fresh.lastIndexOf(", "), fresh.lastIndexOf(" ")) : -1;
        const cut = boundary > 0 ? boundary + 1 : atEnd ? fresh.length : space > 0 ? space + 1 : -1;
        if (cut > 0) {
          const piece = fresh.slice(0, cut).trim();
          if (piece !== "") speak(piece);
          spoken.current.set(node, text.slice(0, done.length + cut));
        }
      };
      const timer = setInterval(() => {
        pump();
        const last = lastChange.current.get(node) ?? Date.now();
        if (Date.now() - last > 600) {
          const text = clean();
          const done = spoken.current.get(node) ?? "";
          const rest = (text.startsWith(done) ? text.slice(done.length) : text).trim();
          if (rest !== "") {
            speak(rest);
            spoken.current.set(node, text);
          }
          clearInterval(timer);
          timers.current.delete(node);
        }
      }, 150);
      timers.current.set(node, timer);
      lastChange.current.set(node, Date.now());
      const observer = new MutationObserver(() => lastChange.current.set(node, Date.now()));
      observer.observe(node, { childList: true, characterData: true, subtree: true });
    };
    document.querySelectorAll(RESPONSE).forEach((node) => seen.current.add(node));
    const root = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of Array.from(record.addedNodes)) {
          if (!(node instanceof Element)) continue;
          if (node.matches(RESPONSE)) watch(node);
          node.querySelectorAll(RESPONSE).forEach(watch);
        }
      }
    });
    root.observe(document.body, { childList: true, subtree: true });
    return () => {
      root.disconnect();
      for (const timer of timers.current.values()) clearInterval(timer);
      timers.current.clear();
    };
  }, [on]);
  const title = on ? ru ? "\u041D\u0435 \u043E\u0437\u0432\u0443\u0447\u0438\u0432\u0430\u0442\u044C \u043E\u0442\u0432\u0435\u0442\u044B" : "Stop reading answers aloud" : ru ? "\u041E\u0437\u0432\u0443\u0447\u0438\u0432\u0430\u0442\u044C \u043E\u0442\u0432\u0435\u0442\u044B" : "Read answers aloud";
  return (0, import_react.createElement)("button", {
    type: "button",
    className: `nm-speak${on ? " nm-speak-on" : ""}`,
    "aria-label": title,
    title,
    "aria-pressed": on,
    onMouseDown: (event) => event.preventDefault(),
    onClick: () => {
      const next = !on;
      patchSettings({ answers: next });
      if (!next) {
        queue.current.length = 0;
        playing.current = false;
      }
    }
  }, (0, import_react.createElement)(
    "svg",
    { width: 17, height: 17, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.75, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true },
    (0, import_react.createElement)("path", { d: "M4 9.5h3.5L12 5.5v13L7.5 14.5H4z" }),
    (0, import_react.createElement)("path", { d: on ? "M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" : "M15.5 9a4 4 0 0 1 0 6" })
  ));
}
function apply(ctx) {
  ensureStyle();
  ctx.slots.inject("conversation.input.right", () => {
    ctx.slots.register({ name: "conversation.input.right", id: "nanomuse-listen", order: 1 }, VoiceListen);
    ctx.slots.register({ name: "conversation.input.right", id: "nanomuse-speak", order: 2 }, VoiceAnswers);
  });
  ctx.slots.inject("settings.section", () => ctx.slots.register({ name: "settings.section", id: "nanomuse-dictation", order: 24, label: () => "\u0414\u0438\u043A\u0442\u043E\u0432\u043A\u0430", locale: "nanomuse" }, DictationSettings));
  ctx.slots.inject("settings.onboarding", () => ctx.slots.register({ name: "settings.onboarding", id: "nanomuse-listen-bridge", order: -1 }, SettingsBridge));
  ctx.slots.inject("conversation.session.header.utilities", () => ctx.slots.register({ name: "conversation.session.header.utilities", id: "nanomuse-chat-cloud", order: -30 }, ChatCloudButton));
}
return module.exports; } });
//# sourceMappingURL=client.js.map
