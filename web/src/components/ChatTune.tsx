/**
 * How this one chat answers: the sampling temperature, carried with the conversation
 * (D8), so a value picked here is the same in the app, on a phone and in the cabinet.
 *
 * The slider is deliberately the whole sheet: *Automatic* follows the value in Settings,
 * and anything else belongs to this chat alone. The relay keeps a per-chat temperature
 * between 0 and 2, the range every OpenAI-shaped provider takes.
 */
import { useEffect, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import type { ThreadMeta } from "../types";
import { Sheet } from "./Sheet";

const MIN = 0;
const MAX = 2;
const STEP = 0.1;

export function ChatTune({
  thread,
  open,
  onClose,
  onSaved,
}: {
  /** The chat being tuned; its own temperature is what the sheet shows. */
  thread: ThreadMeta | null;
  open: boolean;
  onClose: () => void;
  /** The saved chat, for a caller that wants it without waiting for the socket frame. */
  onSaved?: (thread: ThreadMeta) => void;
}) {
  const t = useT();
  const saved = thread?.temperature ?? null;
  // null stands for *Automatic*: whatever Settings says the model should use
  const [value, setValue] = useState<number | null>(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // opening the sheet always shows the chat's own value, never the last chat's
  useEffect(() => {
    if (open) {
      setValue(thread?.temperature ?? null);
      setError("");
    }
  }, [open, thread?.id, thread?.temperature]);

  const save = async (next: number | null) => {
    if (!thread) return;
    setValue(next);
    setBusy(true);
    setError("");
    try {
      // the request is made whether or not the caller wants the row back: `onSaved?.()`
      // around the call itself would skip it when no callback is passed
      const saved = await api.patchThread(thread.id, { temperature: next });
      onSaved?.(saved);
    } catch (e) {
      setError((e as Error).message || t("Could not save"));
      setValue(thread.temperature ?? null);
    } finally {
      setBusy(false);
    }
  };

  const shown = value === null ? t("Automatic") : value.toFixed(1);

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={t("Chat settings")}
      footer={
        <button
          type="button"
          onClick={onClose}
          className="w-full rounded-2xl bg-accent px-4 py-2.5 text-[15px] font-medium text-white disabled:opacity-60"
          disabled={busy}
        >
          {t("Done")}
        </button>
      }
    >
      <div className="pb-2">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-[14.5px] font-medium">{t("Temperature")}</span>
          <span className="rounded-full bg-surface-2 px-2.5 py-0.5 text-[13px] text-fg/80">{shown}</span>
        </div>
        <input
          type="range"
          min={MIN}
          max={MAX}
          step={STEP}
          value={value ?? 1}
          aria-label={t("Temperature")}
          aria-valuetext={shown}
          onChange={(e) => setValue(Number(e.target.value))}
          onPointerUp={() => value !== null && void save(value)}
          onKeyUp={(e) => {
            if (["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(e.key) && value !== null) {
              void save(value);
            }
          }}
          className="mt-2.5 w-full accent-[var(--accent)]"
        />
        <div className="flex justify-between text-[11.5px] text-muted">
          <span>0</span>
          <span>1</span>
          <span>2</span>
        </div>
        <p className="mt-2 text-[12.5px] leading-relaxed text-muted">
          {t("How freely it picks its words: 0 keeps to the safest wording, 2 wanders furthest.")}
        </p>
        <div className="mt-3 flex items-center gap-2">
          <button
            type="button"
            onClick={() => void save(null)}
            aria-pressed={value === null}
            className={
              value === null
                ? "rounded-full bg-accent px-3 py-1.5 text-[13px] font-medium text-white"
                : "rounded-full bg-surface-2 px-3 py-1.5 text-[13px] text-fg/80 hover:bg-surface-2/70"
            }
          >
            {t("Automatic")}
          </button>
          <span className="text-[12px] text-muted">
            {value === null ? t("Follows the value in Settings.") : t("Only this chat.")}
          </span>
        </div>
        {error && <p className="mt-2 text-[12.5px] text-danger">{error}</p>}
      </div>
    </Sheet>
  );
}
