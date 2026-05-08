import { useParams, Link } from "react-router-dom";
import { useSignal, useSignals } from "@/lib/queries";
import { UrgencyBadge } from "@/components/signals/UrgencyBadge";
import { EventTypeKicker } from "@/components/signals/EventTypeKicker";
import { ScorePill } from "@/components/signals/ScorePill";
import { Byline } from "@/components/signals/Byline";
import { ScoreBreakdownPanel } from "@/components/signals/ScoreBreakdownPanel";
import { EntitiesPanel } from "@/components/signals/EntitiesPanel";
import { RelatedSignalsRail } from "@/components/signals/RelatedSignalsRail";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { ArrowLeft } from "lucide-react";

// Signal detail — full reader layout, max-w-3xl body width.
export default function SignalDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: signal, isLoading } = useSignal(id);
  // Wide window so the related list pulls from a meaningful pool, not just the
  // current digest page. useSignals now returns a paged response, so we read
  // `.items` instead of treating the data as a flat array.
  const { data: allSignals } = useSignals({ scope: "all", page: 1 });

  // Related signals share the same event_type but are not the current signal.
  const related =
    allSignals?.items.filter((s) => s.event_type === signal?.event_type && s.id !== id) ?? [];

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (!signal) {
    return (
      <div className="py-20 text-center">
        <p className="font-serif text-h3">Signal not found</p>
        <Link to="/" className="mt-4 inline-flex items-center gap-2 text-accent hover:underline">
          <ArrowLeft className="h-4 w-4" /> Back to digest
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      {/* Back link — always visible so the reader can return to the digest. */}
      <Link to="/" className="mb-8 inline-flex items-center gap-2 text-meta text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> Digest
      </Link>

      {/* Eyebrow: kicker + urgency + score */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <EventTypeKicker type={signal.event_type} />
        <UrgencyBadge urgency={signal.urgency} />
        <ScorePill score={signal.score} />
      </div>

      {/* Headline — display size, full font weight, serif. */}
      <h1 className="mb-4 font-serif text-display font-semibold leading-tight">{signal.title}</h1>

      {/* Dek: short_summary sits between headline and body like a newspaper deck. */}
      {signal.short_summary && (
        <p className="mb-5 font-serif text-dek text-foreground/80">{signal.short_summary}</p>
      )}

      <Byline source={signal.source} publishedAt={signal.published_at} url={signal.url} />

      {/* Hero image if present. */}
      {signal.image_url && (
        <div className="mt-6 overflow-hidden rounded-md border border-border bg-muted">
          <img src={signal.image_url} alt="" loading="lazy" className="aspect-[16/9] w-full object-cover" />
        </div>
      )}

      <Separator className="my-8" />

      {/* Article body — expanded_summary as prose-editorial text. */}
      <div className="prose-editorial">
        {signal.expanded_summary?.split("\n\n").map((para, i) => (
          <p key={i} className={i > 0 ? "mt-4" : ""}>{para}</p>
        ))}
      </div>

      {/* Why it matters — pull-quote block. */}
      {signal.why_it_matters && (
        <blockquote className="my-8 border-l-4 border-accent pl-5 font-serif text-dek text-foreground/90 italic">
          {signal.why_it_matters}
        </blockquote>
      )}

      {/* Scout note — field reporter aside when present. */}
      {signal.scout_note && (
        <p className="my-6 text-ui italic text-muted-foreground">
          <span className="kicker not-italic mr-2">Scout note</span>{signal.scout_note}
        </p>
      )}

      <Separator className="my-8" />

      {/* Score breakdown — collapsed by default so it doesn't distract. */}
      <div className="mb-8">
        <ScoreBreakdownPanel score={signal.score} breakdown={signal.score_breakdown} />
      </div>

      {/* Entities panel. */}
      {Object.keys(signal.entities ?? {}).length > 0 && (
        <div className="mb-10">
          <div className="kicker mb-3">Entities</div>
          <EntitiesPanel entities={signal.entities} />
        </div>
      )}

      {/* Related signals rail. */}
      {related.length > 0 && (
        <>
          <Separator className="my-8" />
          <RelatedSignalsRail signals={related} />
        </>
      )}
    </div>
  );
}
