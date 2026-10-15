import { intlLocale, useT } from "../i18n";
import type { ResearchConflict, ResearchEvidence } from "../research-api";

/** Conflicts annotate facts without removing either side from the evidence ledger. */
export function ResearchConflicts({ conflicts, evidence }: {
  conflicts: ResearchConflict[];
  evidence: ResearchEvidence[];
}) {
  const t = useT();
  const open = conflicts.filter((item) => item.status === "open");
  const previous = conflicts.filter((item) => item.status === "superseded");
  const byId = new Map(evidence.map((item) => [item.id, item]));

  return (
    <section className="mb-5 space-y-3 border-b border-border pb-5" aria-label={t("Evidence conflicts")}>
      <h4 className="text-[14px] font-semibold">{t("Evidence conflicts")}</h4>
      {open.length > 0 ? (
        <>
          <p className="text-[12px] leading-relaxed text-muted">{t("Both facts remain in the ledger. Decisions affected by an open conflict stay unknown.")}</p>
          {open.map((item) => <ConflictCard key={item.id} conflict={item} evidence={byId} />)}
        </>
      ) : <p className="text-[13px] text-muted">{t("No open evidence conflicts.")}</p>}
      {previous.length > 0 && (
        <details className="rounded-2xl border border-border p-3">
          <summary className="cursor-pointer text-[12px] font-medium">{t("Previous conflicts ({n})", { n: previous.length })}</summary>
          <div className="mt-3 space-y-3">
            {previous.map((item) => <ConflictCard key={item.id} conflict={item} evidence={byId} />)}
          </div>
        </details>
      )}
    </section>
  );
}

function ConflictCard({ conflict, evidence }: {
  conflict: ResearchConflict;
  evidence: Map<string, ResearchEvidence>;
}) {
  const t = useT();
  const date = new Date(conflict.created_at);
  return (
    <article aria-label={t("Evidence conflict {id}", { id: conflict.id })} className="space-y-3 rounded-2xl border border-border bg-surface-2/50 p-3 sm:p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h5 className="text-[13px] font-semibold">{conflict.dimension}</h5>
        <span className="rounded-full border border-border px-2 py-1 text-[11px] text-muted">{t(conflict.status === "open" ? "Open conflict" : "Superseded conflict")}</span>
      </div>
      <p className="whitespace-pre-wrap text-[14px] leading-relaxed">{conflict.reason}</p>
      <dl className="space-y-1 text-[12px]">
        <div><dt className="inline text-muted">{t("Repository")}: </dt><dd className="inline break-all">{conflict.candidate}</dd></div>
        <div><dt className="inline text-muted">{t("Version scope")}: </dt><dd className="inline whitespace-pre-wrap break-words">{conflict.version_scope || t("Not specified")}</dd></div>
      </dl>
      <div>
        <h6 className="mb-2 text-[12px] font-medium">{t("Referenced evidence")}</h6>
        <ul className="space-y-2">
          {conflict.evidence_refs.map((id) => {
            const item = evidence.get(id);
            return (
              <li key={id} className="rounded-xl border border-border bg-surface p-3">
                <code className="break-all text-[11px] text-muted">{id}</code>
                {item ? (
                  <>
                    <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed">{item.fact}</p>
                    {item.quote && <blockquote className="mt-2 border-l-2 border-border pl-2 text-[12px] text-muted">{item.quote}</blockquote>}
                    {item.version && <p className="mt-2 break-words text-[11px] text-muted">{t("Source version")}: {item.version}</p>}
                    {safeSourceUrl(item.source_url) && <a href={item.source_url} target="_blank" rel="noopener noreferrer" className="mt-1 block break-all text-[11px] text-accent hover:underline">{item.source_url}</a>}
                  </>
                ) : <p className="mt-1 text-[12px] text-muted">{t("This evidence is not available in the current ledger.")}</p>}
              </li>
            );
          })}
        </ul>
      </div>
      <time dateTime={conflict.created_at} className="block text-[11px] text-muted">{Number.isNaN(date.getTime()) ? conflict.created_at : date.toLocaleString(intlLocale())}</time>
    </article>
  );
}

function safeSourceUrl(value: string): boolean {
  try { return ["https:", "http:"].includes(new URL(value).protocol); }
  catch { return false; }
}
