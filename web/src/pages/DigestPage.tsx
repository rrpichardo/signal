import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useDisplaySettings, useExecutiveBriefing, useLatestRun, useSignals } from "@/lib/queries";
import { FeaturedSignal } from "@/components/signals/FeaturedSignal";
import { SignalListItem } from "@/components/signals/SignalListItem";
import { Pagination } from "@/components/Pagination";
import { MarkdownBlock, MarkdownInline } from "@/components/Markdown";
import { Skeleton } from "@/components/ui/skeleton";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AgentRun, ExecutiveBriefing } from "@/lib/types";

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
  // Sparse-run hint: when the latest run produced only a small handful of
  // signals, point the reader to All time so they don't think the product is
  // broken. The number comes from the latest run's summary (signal_count),
  // which is the run's own self-report — not the current page slice.
  const latestRunSignalCount = response?.run?.signal_count ?? null;
  const showSparseHint =
    scope === "latest" &&
    page === 1 &&
    latestRunSignalCount !== null &&
    latestRunSignalCount > 0 &&
    latestRunSignalCount < 3;

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

      {/* Sparse-run hint — when the latest run produced only 1 or 2 signals,
          most readers want to see the broader feed. Surface All time before
          the briefing so the reader knows there's more to look at. */}
      {showSparseHint && (
        <div className="mb-6 rounded-md border border-dashed border-border bg-muted/10 px-4 py-3 text-meta text-muted-foreground">
          Latest run found {latestRunSignalCount} new{" "}
          {latestRunSignalCount === 1 ? "signal" : "signals"} — see{" "}
          <button
            type="button"
            onClick={() => setScope("all")}
            className="underline underline-offset-2 hover:text-foreground"
          >
            All time
          </button>{" "}
          for the full feed.
        </div>
      )}

      {/* Editor briefing — model-written narrative above the digest list. Shown
          on page 1 of the latest scope when the Editor has run. The backend
          may serve a *prior* run's briefing tagged stale=true when the latest
          run was too weak; we render a "showing previous briefing" note in
          that case instead of pretending the briefing is fresh. */}
      {scope === "latest" && page === 1 && (briefingData?.briefing_status === "generated" || briefingData?.briefing_status === "partial") && briefingData.briefing && hasRenderableBriefingContent(briefingData.briefing) && (
        <BriefingBlock
          briefing={briefingData.briefing}
          status={briefingData.briefing_status}
          stale={briefingData.stale}
          staleRunStartedAt={briefingData.stale_run_started_at}
        />
      )}

      {/* Missing-briefing hint — fires when the latest run has no usable
          briefing. Covers three cases: status is pending/failed/skipped, the
          DB column is NULL for pre-Phase-3 runs, OR the briefing exists but
          has no renderable content (off-schema or empty after normalization).
          The renderable guard prevents a silent blank card. */}
      {scope === "latest" && page === 1 && briefingData &&
       (
         (briefingData.briefing_status !== "generated" && briefingData.briefing_status !== "partial") ||
         !briefingData.briefing ||
         !hasRenderableBriefingContent(briefingData.briefing)
       ) && (
        <div className="mb-8 rounded-md border border-dashed border-border bg-muted/10 px-4 py-3 text-meta text-muted-foreground">
          Intelligence briefing not generated for this run — re-run the agent to produce one.
        </div>
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

// Model-written briefing from the Editor worker. Renders above the digest
// list when the Editor has produced a briefing for the latest run — or, when
// stale=true, when the backend served a *prior* run's briefing because the
// latest run was too weak. The stale note tells the reader the briefing is
// not fresh, so they don't think the dashboard is broken.
// True only when the briefing has enough content to render meaningfully.
// Without this guard, an off-schema or fully-coerced-empty briefing renders as
// a bare "Intelligence briefing" card header with nothing underneath.
function hasRenderableBriefingContent(b: ExecutiveBriefing): boolean {
  const headline = (b.headline || "").trim();
  if (!headline) return false;
  return Boolean(
    (b.summary || "").trim() ||
    (b.key_takeaways && b.key_takeaways.length > 0) ||
    (b.briefing_paragraphs && b.briefing_paragraphs.length > 0)
  );
}

function BriefingBlock({
  briefing,
  status,
  stale,
  staleRunStartedAt,
}: {
  briefing: ExecutiveBriefing;
  status: string;
  stale: boolean;
  staleRunStartedAt: string | null;
}) {
  return (
    <div className="mb-8 rounded-md border border-border bg-muted/20 p-6">
      <div className="kicker mb-3">Intelligence briefing</div>

      {/* Stale-briefing note — only when this briefing came from a prior run. */}
      {stale && staleRunStartedAt && (
        <p className="mb-4 text-meta text-muted-foreground">
          Not enough new signal to update — showing previous briefing from {relativeTime(staleRunStartedAt)}.
        </p>
      )}

      {/* Headline — the single most consequential development today. */}
      <p className="mb-3 font-serif text-h3 font-semibold leading-snug text-foreground">
        {briefing.headline}
      </p>

      {/* Summary — macro story under the headline. */}
      {briefing.summary && (
        <MarkdownBlock className="mb-5 text-body text-foreground/85">{briefing.summary}</MarkdownBlock>
      )}

      {/* Key takeaways — the punchy "you only read this" bullets. Prominent
          styling because these are the highest-value content in the briefing. */}
      {briefing.key_takeaways && briefing.key_takeaways.length > 0 && (
        <div className="mb-6">
          <div className="kicker mb-2">Key takeaways</div>
          <ul className="list-disc space-y-1.5 pl-5">
            {briefing.key_takeaways.map((item, i) => (
              <li key={i} className="text-ui font-medium text-foreground"><MarkdownInline>{item}</MarkdownInline></li>
            ))}
          </ul>
        </div>
      )}

      {/* Themed sections — each has a subhead, framing body, and nested bullets.
          This is where the bulk of the synthesis lives. */}
      {briefing.briefing_paragraphs && briefing.briefing_paragraphs.length > 0 && (
        <div className="mb-6 space-y-5">
          {briefing.briefing_paragraphs.map((section, i) => (
            <div key={i}>
              {section.heading && (
                <h3 className="mb-1 font-serif text-dek font-semibold text-foreground">
                  {section.heading}
                </h3>
              )}
              {section.body && (
                <MarkdownBlock className="mb-2 text-body text-foreground/85">{section.body}</MarkdownBlock>
              )}
              {section.bullets && section.bullets.length > 0 && (
                <ul className="list-disc space-y-1 pl-5">
                  {section.bullets.map((b, j) => (
                    <li key={j} className="text-ui text-foreground/85"><MarkdownInline>{b}</MarkdownInline></li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Key themes — skim-view chip strip. Tight labels, 1-sentence summaries. */}
      {briefing.key_themes && briefing.key_themes.length > 0 && (
        <div className="mb-6">
          <div className="kicker mb-2">Key themes</div>
          <ul className="space-y-2">
            {briefing.key_themes.map((theme, i) => (
              <li key={i} className="text-ui">
                <span className="font-medium text-foreground">{theme.label}</span>
                {theme.summary && (
                  <span className="ml-2 text-muted-foreground">— <MarkdownInline>{theme.summary}</MarkdownInline></span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Insights — second-order observations and cross-signal patterns. */}
      {briefing.insights && briefing.insights.length > 0 && (
        <div className="mb-6">
          <div className="kicker mb-2">Insights</div>
          <ul className="list-disc space-y-1 pl-5">
            {briefing.insights.map((item, i) => (
              <li key={i} className="text-ui italic text-foreground/80"><MarkdownInline>{item}</MarkdownInline></li>
            ))}
          </ul>
        </div>
      )}

      {/* Cross-signal narrative — closing synthesis. */}
      {briefing.cross_signal_narrative && (
        <MarkdownBlock className="mb-5 text-body text-foreground/80">
          {briefing.cross_signal_narrative}
        </MarkdownBlock>
      )}

      {/* Watch items — forward-looking alerts. */}
      {briefing.watch_items && briefing.watch_items.length > 0 && (
        <div className="mb-4">
          <div className="kicker mb-2">Watch</div>
          <ul className="list-disc space-y-1 pl-5">
            {briefing.watch_items.map((item, i) => (
              <li key={i} className="text-ui text-foreground/85"><MarkdownInline>{item}</MarkdownInline></li>
            ))}
          </ul>
        </div>
      )}

      {/* Footnotes: partial coverage and truncation. */}
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
