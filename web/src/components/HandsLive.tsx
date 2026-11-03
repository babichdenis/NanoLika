/**
 * The hands at work, as they work: a slim bar over the composer while this chat is driving
 * the computer, naming the last action and offering Stop. The chat's own *Hands* card is
 * the record of the task (one card, updated step by step); this bar is the live frame
 * stream (`handsLive`) — without it a person reading the chat had no sign of the hands
 * until the card caught up, and no Stop in reach.
 */
import { Hand, Loader2, Square } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { describeHandsStep } from "./Cards";
import { useT } from "../i18n";
import type { HandsLive } from "../types";

export function HandsLiveBar({ live, name }: { live: HandsLive | null; name: string }) {
  const t = useT();
  const [stopping, setStopping] = useState(false);
  // `end`/`stop` clear the store's copy; the bar also hides on a `notice` (the hands ask
  // the person something and the turn waits)
  if (!live) return null;
  const step = live.action ? describeHandsStep({ action: live.action, label: live.label, text: live.text }, t) : (live.label ?? "");
  const where = [live.app, live.title].filter(Boolean).join(" · ");
  const stop = async () => {
    setStopping(true);
    try {
      await api.stopHands();
    } catch {
      /* the runtime answers the stop with its own `end` frame */
    } finally {
      setStopping(false);
    }
  };
  return (
    <div className="mx-3 mb-1 flex items-center gap-2 rounded-2xl border border-accent/40 bg-accent/8 px-3 py-2">
      <span className="shrink-0 text-accent">
        <Hand size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12.5px] font-medium">
          {t("{name} is using this computer", { name })}
          {where && <span className="text-muted"> · {where}</span>}
        </div>
        {step && <div className="truncate text-[12px] text-muted">{step}</div>}
      </div>
      <button
        type="button"
        onClick={() => void stop()}
        disabled={stopping}
        className="flex shrink-0 items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 text-[12px] font-medium text-fg shadow-sm disabled:opacity-60"
      >
        {stopping ? <Loader2 size={12} className="animate-spin" /> : <Square size={11} fill="currentColor" />}
        {t("Stop")}
      </button>
    </div>
  );
}
