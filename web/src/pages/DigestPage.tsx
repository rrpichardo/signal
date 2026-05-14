import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useDisplaySettings, useExecutiveBriefing, useExecutiveSummary, useLatestRun, useSignals } from "@/lib/queries";
import { FeaturedSignal } from "@/components/signals/FeaturedSignal";
import { SignalListItem } from "@/components/signals/SignalListItem";
import { Pagination } from "@/components/Pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AgentRun, ExecutiveBriefing, Signal } from "@/lib/types";

type Scope = "latest" | "all";

// Digest page — the editorial home screen.
// Layout: kicker header + scope toggle -> featured signal -> numbered list ->
// pagination at bottom. Reads `default_scope` and `page_size` from
// /api/display-settings so the user can change either without code edits.
export default function DigestPage() {
  // Display settings drive default scope and (server-side) page size.
  const { data: displaySettings } = useDisplaySettings();

  const [scope, setScope] = useState<Scope>("latest");
  const [page, setPage] = useState(1);

  // Sync local scope state with the user's saved default once it loads.
  // Only runs on initial settings load so toggling doesn't fight the user.
  const [defaultScopeApplied, setDefaultScopeApplied] = useState(false);
  useEffect(() => {
    if (!defaultScopeApplied && displaySettings?.default_scope) {
      setScope(displaySettings.default_scope);
      setDefaultScopeApplied(true);
    }
  }, [defaultScopeApplied, displaySettings?.default_scope]);

  // Reset to page 1 when scope changes — a different list shouldn't deep-link
  // into a page that may not exist in the new view.
  useEffect(() => setPage(1), [scope]);

  const { data: response, isLoading, error } = useSignals({ scope, page });
  const { data: run } = useLatestRun();
  const { data: execSignals } = useExecutiveSummary();
  const { data: briefingData } = useExecutiveBriefing();

  const hasRun = run && "id" in run;
  const latestAgentRun = hasRun ? (run as AgentRun) : null;
  const isRunning = latestAgentRun?.status === "running";

  // Featured-card treatment only on page 1 of the latest scope. On archive
  // pages or all-time browsing, treating the first item as "the lead story"
  // is misleading — render every item as a uniform list instead.
  const items = response?.items ?? [];
  const showFeatured = scope === "latest" && page === 1 && items.length > 0;
  const featured = showFeatured ? items[0] : undefined;
  const rest = showFeatured ? items.slice(1) : items;

  if (isLoading) {
    return (
      <div className="space-y-10">
        <div className="space-y-3">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-12 w-3/4" />
          <Skeleton className="h-6 w-1/2" />
        </div>
        {[1, 2, 3].map((i) => (
          <div key={i} className="flex gap-5 border-b border-border py-7">
            <Skeleton className="h-8 w-6 hidden sm:block" />
            <div className="flex-1 space-y-3">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-20 text-center">
        <p className="font-serif text-h3 text-foreground">Could not load signals</p>
        <p className="mt-2 text-ui text-muted-foreground">{String(error)}</p>
      </div>
    );
  }

  // Header subhead: "Updated <relative> . <total> signals". Total comes from
  // the paged response so the count is real, not the array length.
  const total = response?.total ?? 0;
  const totalPages = response?.total_pages ?? 0;
  const runStartedAt = response?.run?.started_at ?? latestAgentRun?.started_at;

  return (
    <div>
      {/* Section header: digest label, freshness, scope toggle */}
      <div className="mb-8 flex flex-wrap items-baseline justify-between gap-4">
        <div>
          <div className="kicker">Digest — since last run</div>
          {runStartedAt ? (
            <Link
              to="/activity"
              className="mt-1 inline-block text-meta text-muted-foreground hover:text-foreground"
            >
              Updated {relativeTime(runStartedAt)} · {total} {total === 1 ? "signal" : "signals"}
            </Link>
          ) : (
            <p className="mt-1 text-meta text-muted-foreground">No runs yet</p>
          )}
        </div>

        {/* Scope toggle — Latest run vs All time. Default lives in /api/display-settings. */}
        <ScopeToggle scope={scope} onChange={setScope} />
      </div>

      {/* Run-in-progress banner — gentle hint that the digest may shift soon. */}
      {isRunning && scope === "latest" && (
        <div className="mb-6 rounded-md border border-accent/30 bg-accent/5 px-4 py-3 text-meta text-foreground/80">
          Agent run in progress — digest will update when complete.
        </div>
      )}

      {/* Editor briefing — model-written narrative above the exec list.
          Only shown on page 1 of the latest scope when the Editor has run. */}
      {scope === "latest" && page === 1 && (briefingData?.briefing_status === "generated" || briefingData?.briefing_status === "partial") && briefingData.briefing && (
        <BriefingBlock briefing={briefingData.briefing} status={briefingData.briefing_status} />
      )}

      {/* Missing-briefing hint — shown when the latest run has no briefing
          (status is pending / failed / skipped, or the column is NULL for
          pre-Phase-3 runs). Tells the reader the feature exists but didn't
          produce output this run, instead of silently hiding. */}
      {scope === "latest" && page === 1 && briefingData &&
       briefingData.briefing_status !== "generated" &&
       briefingData.briefing_status !== "partial" && (
        <div className="mb-8 rounded-md border border-dashed border-border bg-muted/10 px-4 py-3 text-meta text-muted-foreground">
          Intelligence briefing not generated for this run — re-run the agent to produce one.
        </div>
      )}

      {/* Executive summary — top 12 signals at a glance. Only shown on page 1 of latest scope. */}
      {scope === "latest" && page === 1 && execSignals && execSignals.length > 0 && (
        <ExecSummaryBlock signals={execSignals} />
      )}

      {/* Empty state — when the run produced 0 signals, or there are no signals at all. */}
      {total === 0 ? (
        <EmptyState scope={scope} />
      ) : (
        <>
          {featured && <FeaturedSignal signal={featured} />}

          {rest.length > 0 && (
            <div className={cn(showFeatured && "mt-12")}>
              {showFeatured && <div className="kicker mb-0">More signals</div>}
              {rest.map((signal, i) => (
                <SignalListItem
                  key={signal.id}
                  signal={signal}
                  // Rank numerals continue across pages so page 2 starts at a
                  // sensible offset, not 1 again.
                  rank={(page - 1) * (response?.page_size ?? 10) + i + (showFeatured ? 2 : 1)}
                />
              ))}
            </div>
          )}

          <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
        </>
      )}
    </div>
  );
}

// --- Sub-components ---------------------------------------------------------

function ScopeToggle({ scope, onChange }: { scope: Scope; onChange: (s: Scope) => void }) {
  const base =
    "px-3 py-1.5 text-ui transition-colors first:rounded-l-sm last:rounded-r-sm border border-border";
  const active = "bg-foreground text-background";
  const inactive = "bg-background text-muted-foreground hover:text-foreground";
  return (
    <div className="inline-flex" role="radiogroup" aria-label="Digest scope">
      <button
        type="button"
        onClick={() => onChange("latest")}
        aria-checked={scope === "latest"}
        role="radio"
        className={cn(base, scope === "latest" ? active : inactive)}
      >
        Latest run
      </button>
      <button
        type="button"
        onClick={() => onChange("all")}
        aria-checked={scope === "all"}
        role="radio"
        className={cn(base, "border-l-0", scope === "all" ? active : inactive)}
      >
        All time
      </button>
    </div>
  );
}

// Model-written briefing from the Editor worker. Renders above the exec list
// when the Editor has produced a briefing for the latest run.
function BriefingBlock({ briefing, status }: { briefing: ExecutiveBriefing; status: string }) {
  return (
    <div className="mb-8 rounded-md border border-border bg-muted/20 p-6">
      <div className="kicker mb-3">Intelligence briefing</div>

      {/* Headline */}
      <p className="mb-4 font-serif text-h3 font-semibold leading-snug text-foreground">
        {briefing.headline}
      </p>

      {/* Narrative paragraphs */}
      {briefing.briefing_paragraphs?.length > 0 && (
        <div className="mb-5 space-y-3">
          {briefing.briefing_paragraphs.map((para, i) => (
            <p key={i} className="text-body text-foreground/85">{para}</p>
          ))}
        </div>
      )}

      {/* Key themes */}
      {briefing.key_themes?.length > 0 && (
        <div className="mb-5">
          <div className="kicker mb-2">Key themes</div>
          <ul className="space-y-2">
            {briefing.key_themes.map((theme, i) => (
              <li key={i} className="text-ui">
                <span className="font-medium text-foreground">{theme.label}</span>
                {theme.summary && (
                  <span className="ml-2 text-muted-foreground">— {theme.summary}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Watch items */}
      {briefing.watch_items?.length > 0 && (
        <div className="mb-4">
          <div className="kicker mb-2">Watch</div>
          <ul className="list-disc space-y-1 pl-5">
            {briefing.watch_items.map((item, i) => (
              <li key={i} className="text-ui text-foreground/85">{item}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Footnotes: partial coverage warning, truncation note */}
      <div className="mt-4 space-y-1">
        {status === "partial" && (
          <p className="text-meta text-muted-foreground">
            Some signals were summarized without full article text — coverage may be incomplete.
          </p>
        )}
        {briefing.any_artifact_truncated && (
          <p className="text-meta text-muted-foreground">
            One or more articles were truncated during analysis.
          </p>
        )}
      </div>
    </div>
  );
}

// Compact numbered list of top-12 signals. Gives readers a scannable headline
// view before they commit to the full featured card below.
function ExecSummaryBlock({ signals }: { signals: Signal[] }) {
  return (
    <div className="mb-10 rounded-md border border-border bg-muted/30 p-5">
      <div className="kicker mb-3">Executive summary — top {signals.length}</div>
      <ol className="space-y-2">
        {signals.map((s, i) => (
          <li key={s.id} className="flex items-baseline gap-3 text-ui">
            <span className="w-5 shrink-0 text-right font-mono text-muted-foreground/60 text-xs">{i + 1}</span>
            <Link
              to={`/signal/${encodeURIComponent(s.id)}`}
              className="flex-1 text-foreground hover:text-accent hover:underline"
            >
              {s.title}
            </Link>
            <span className="shrink-0 font-mono text-xs text-muted-foreground">{s.score}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function EmptyState({ scope }: { scope: Scope }) {
  return (
    <div className="py-16 text-center">
      <p className="font-serif text-h3 text-foreground">No signals yet</p>
      <p className="mx-auto mt-3 max-w-md text-body text-muted-foreground">
        {scope === "latest"
          ? "The latest run produced no signals. Try All time, or run the agent again."
          : "No signals in the database. Run the agent to start collecting."}
      </p>
      <pre className="mt-6 inline-block rounded-md border border-border bg-muted px-6 py-4 font-mono text-xs text-foreground/80">
        python3 -m signal_stream agent run
      </pre>
    </div>
  );
}
