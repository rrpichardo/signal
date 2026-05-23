import { useState } from "react";
import {
  useSources,
  useTestSource,
  useTestAllSources,
  useToggleSource,
  useRemoveSource,
} from "@/lib/queries";
import { Source } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { pushToast } from "@/hooks/use-toast";
import { CircleHelp, Lock, Unlock } from "lucide-react";

// Renders a colored status badge based on the source's last health check.
function HealthBadge({ health }: { health: Source["health"] }) {
  if (!health) {
    return <Badge variant="outline" className="text-muted-foreground">Not checked</Badge>;
  }

  // Each status gets a distinct muted color so the table is scannable at a glance.
  const styles: Record<string, string> = {
    ok: "bg-green-500/10 text-green-700 border-green-300",
    error: "bg-red-500/10 text-red-700 border-red-300",
    paywall: "bg-yellow-500/10 text-yellow-700 border-yellow-300",
    empty: "bg-slate-500/10 text-slate-500 border-slate-200",
    skipped: "bg-slate-500/10 text-slate-500 border-slate-200",
  };

  const label = health.status ?? "unknown";
  return (
    <Badge variant="outline" className={styles[label] ?? ""}>
      {label}
    </Badge>
  );
}

// Shows the paid/free result directly instead of hiding it inside health.
function PaidBadge({ health }: { health: Source["health"] }) {
  if (!health) {
    return (
      <Badge variant="outline" className="gap-1 text-muted-foreground">
        <CircleHelp className="h-3 w-3" aria-hidden="true" />
        Unknown
      </Badge>
    );
  }

  if (health.paywall_detected || health.status === "paywall") {
    return (
      <Badge
        variant="outline"
        className="gap-1 border-yellow-300 bg-yellow-500/10 text-yellow-700"
      >
        <Lock className="h-3 w-3" aria-hidden="true" />
        Paid
      </Badge>
    );
  }

  if (health.status === "ok" || health.status === "empty") {
    return (
      <Badge
        variant="outline"
        className="gap-1 border-green-300 bg-green-500/10 text-green-700"
      >
        <Unlock className="h-3 w-3" aria-hidden="true" />
        Free
      </Badge>
    );
  }

  return (
    <Badge variant="outline" className="gap-1 text-muted-foreground">
      <CircleHelp className="h-3 w-3" aria-hidden="true" />
      Unknown
    </Badge>
  );
}

// SourcesPage — table of every configured source with health status, toggle, and
// per-source or bulk test actions. Mutations hit the Python dashboard API and
// invalidate the "sources" query key so the table refreshes automatically.
export default function SourcesPage() {
  const { data: sources = [], isLoading } = useSources();
  const testOne = useTestSource();
  const testAll = useTestAllSources();
  const toggle = useToggleSource();
  const remove = useRemoveSource();

  // Local flag prevents button spam while the bulk test is in flight.
  const [testingAll, setTestingAll] = useState(false);

  // Kick the bulk health check and summarize results in a toast.
  function handleTestAll() {
    setTestingAll(true);
    testAll.mutate(undefined, {
      onSuccess: (data) => {
        const failed = data.results.filter(
          (r) => r.status !== "ok" && r.status !== "empty",
        );
        const passed = data.results.length - failed.length;
        pushToast({
          title: "Health check complete",
          description: `${passed} passed · ${failed.length} failed`,
        });
      },
      onError: (err) =>
        pushToast({
          title: "Health check failed",
          description: String(err),
          variant: "destructive",
        }),
      onSettled: () => setTestingAll(false),
    });
  }

  // Toggle a single source on/off; the mutation writes through to the TOML config.
  function handleToggle(source: Source) {
    toggle.mutate(
      { id: source.id, enabled: !source.enabled },
      {
        onError: (err) =>
          pushToast({
            title: "Toggle failed",
            description: String(err),
            variant: "destructive",
          }),
      },
    );
  }

  // Test a single source and show the result (status + article count or error).
  function handleTest(source: Source) {
    testOne.mutate(source.id, {
      onSuccess: (result) => {
        const detail =
          result.error_msg
            ? result.error_msg
            : `${result.article_count} article${result.article_count !== 1 ? "s" : ""}`;
        pushToast({
          title: `${source.name}: ${result.status}`,
          description: detail,
        });
      },
      onError: (err) =>
        pushToast({
          title: "Test failed",
          description: String(err),
          variant: "destructive",
        }),
    });
  }

  // Confirm before removing — this writes through to the TOML and cannot be undone.
  function handleRemove(source: Source) {
    if (!window.confirm(`Remove "${source.name}"? This cannot be undone.`)) return;
    remove.mutate(source.id, {
      onSuccess: () =>
        pushToast({ title: "Source removed", description: source.name }),
      onError: (err) =>
        pushToast({
          title: "Remove failed",
          description: String(err),
          variant: "destructive",
        }),
    });
  }

  if (isLoading) {
    return (
      <div className="p-8 text-muted-foreground text-sm">Loading sources…</div>
    );
  }

  const enabledCount = sources.filter((s) => s.enabled).length;

  return (
    <div className="space-y-6">
      {/* Header row: title + summary counts + bulk test button */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Sources</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {sources.length} configured · {enabledCount} enabled
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleTestAll}
          disabled={testingAll}
        >
          {testingAll ? "Checking…" : "Test All Sources"}
        </Button>
      </div>

      {/* Sources table — one row per source. */}
      <div className="overflow-x-auto rounded-md border">
        <table className="min-w-[960px] w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/40">
              <th className="px-4 py-2 text-left font-medium text-muted-foreground">Name</th>
              <th className="px-4 py-2 text-left font-medium text-muted-foreground">Paid?</th>
              <th className="px-4 py-2 text-left font-medium text-muted-foreground">Kind</th>
              <th className="px-4 py-2 text-left font-medium text-muted-foreground">Group</th>
              <th className="px-4 py-2 text-left font-medium text-muted-foreground">Health</th>
              <th className="px-4 py-2 text-left font-medium text-muted-foreground">Articles</th>
              <th className="px-4 py-2 text-center font-medium text-muted-foreground">Enabled</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {sources.map((source) => (
              <tr
                key={source.id}
                className="border-b last:border-0 hover:bg-muted/20"
              >
                {/* Name cell: full name + truncated URL underneath for context. */}
                <td className="px-4 py-3 font-medium">
                  <div>{source.name}</div>
                  {source.url && (
                    <div className="text-xs text-muted-foreground truncate max-w-[220px]">
                      {source.url}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3">
                  <PaidBadge health={source.health} />
                </td>
                <td className="px-4 py-3 text-muted-foreground">{source.kind}</td>
                <td className="px-4 py-3 text-muted-foreground">{source.group}</td>
                {/* Health cell: badge + truncated error message if present. */}
                <td className="px-4 py-3">
                  <HealthBadge health={source.health} />
                  {source.health?.error_msg && (
                    <div
                      className="text-xs text-muted-foreground mt-0.5 max-w-[180px] truncate"
                      title={source.health.error_msg}
                    >
                      {source.health.error_msg}
                    </div>
                  )}
                </td>
                {/* Article count: dash until the source has been tested. */}
                <td className="px-4 py-3 text-muted-foreground">
                  {source.health ? source.health.article_count : "—"}
                </td>
                <td className="px-4 py-3 text-center">
                  <Switch
                    checked={source.enabled}
                    onCheckedChange={() => handleToggle(source)}
                    disabled={toggle.isPending}
                  />
                </td>
                {/* Action buttons: Test (non-destructive) and Remove (destructive). */}
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleTest(source)}
                      disabled={testOne.isPending}
                    >
                      Test
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => handleRemove(source)}
                    >
                      Remove
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
