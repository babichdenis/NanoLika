import { fundsView, type FundsInfo } from "../../../cloud/nanomuse_cloud/web_client/funds.js";
import { useLocale } from "../i18n";
import { useStore } from "../store";

/** A priced refusal is separate from the free-allowance card; retry is always explicit. */
export function FundsNotice({ info }: { info: FundsInfo }) {
  const { setTab } = useStore();
  const locale = useLocale();
  const view = fundsView(info, locale);
  if (!view) return null;
  return <div className="rise mx-6 space-y-2 rounded-2xl bg-amber-500/12 p-3 text-sm">
    <p>{view.message}</p>
    <div className="flex flex-wrap gap-3">
      <a href={view.topupUrl} target="_blank" rel="noopener noreferrer" className="font-semibold underline">{view.topupLabel}</a>
      <button type="button" onClick={() => setTab("connections")} className="underline">{view.cheaperLabel}</button>
    </div>
  </div>;
}
