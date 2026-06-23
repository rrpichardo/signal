import { useState } from "react";
import {
  useSources,
  useTestSource,
  useTestAllSources,
  useToggleSource,
  useRemoveSource,
  useAddSource,
} from "@/lib/queries";
import { Source } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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

const SOURCE_KINDS = ["rss", "atom", "youtube", "html_scrape"] as const;

// Resolve pasted YouTube input to a channel id (UC…). Returns handleBlocked when
// the user gave an @handle or /c/ vanity URL we can't resolve without an API call
// — that's the predictable wrong input we want to catch before a dead source is saved.
function parseYouTubeChannelId(
  input: string,
): { channelId?: string; handleBlocked?: boolean } {
  const v = input.trim();
  if (!v) return {};
  if (/^UC[\w-]{20,}$/.test(v)) return { channelId: v };
  const m = v.match(/youtube\.com\/channel\/(UC[\w-]{20,})/i);
  if (m) return { channelId: m[1] };
  if (/@[\w.-]+/.test(v) || /youtube\.com\/(c|user)\//i.test(v)) {
    return { handleBlocked: true };
  }
  return {};
}

// Inline form for adding a source. All fields show at once, labeled by which
// kind they apply to — no conditional rendering, so there's no hidden-field state
// to get wrong. Submit validates per-kind and resolves YouTube channel input.
function AddSourceForm({ onClose }: { onClose: () => void }) {
  const add = useAddSource();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<string>("rss");
  const [group, setGroup] = useState("");
  const [url, setUrl] = useState("");
  const [channelId, setChannelId] = useState("");
  const [linkPattern, setLinkPattern] = useState("");
  const [limit, setLimit] = useState("8");
  const [onDemand, setOnDemand] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function submit() {
    setError(null);
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }

    const payload: {
      name: string;
      kind: string;
      group?: string;
      url?: string;
      channel_id?: string;
      article_link_pattern?: string;
      limit?: number;
      on_demand?: boolean;
    } = {
      name: name.trim(),
      kind,
      limit: Number(limit) || 8,
      on_demand: onDemand,
    };
    if (group.trim()) payload.group = group.trim();

    if (kind === "rss" || kind === "atom") {
      if (!url.trim()) {
        setError("RSS/Atom feeds need a URL.");
        return;
      }
      payload.url = url.trim();
    } else if (kind === "youtube") {
      // Accept a raw channel id or a channel URL from either field; block @handles.
      const fromChannel = channelId.trim() ? parseYouTubeChannelId(channelId) : {};
      const fromUrl = url.trim() ? parseYouTubeChannelId(url) : {};
      if (fromChannel.handleBlocked || fromUrl.handleBlocked) {
        setError(
          "@handles aren't supported — paste the channel ID (starts with UC) or a youtube.com/channel/UC… URL.",
        );
        return;
      }
      const cid = fromChannel.channelId || fromUrl.channelId;
      if (!cid) {
        setError(
          "Enter a YouTube channel ID (starts with UC) or a youtube.com/channel/UC… URL.",
        );
        return;
      }
      payload.channel_id = cid;
      payload.url = `https://www.youtube.com/feeds/videos.xml?channel_id=${cid}`;
    } else if (kind === "html_scrape") {
      if (!url.trim() || !linkPattern.trim()) {
        setError("Scrape sources need both a URL and a link pattern.");
        return;
      }
      payload.url = url.trim();
      payload.article_link_pattern = linkPattern.trim();
    }

    add.mutate(payload, {
      onSuccess: () => {
        pushToast({ title: "Source added", description: name.trim() });
        onClose();
      },
      onError: (err) => setError(String(err)),
    });
  }

  return (
    <div className="rounded-md border p-4 space-y-4 bg-muted/20">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="src-name">Name</Label>
          <Input
            id="src-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Latent Space"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="src-kind">Kind</Label>
          <Select value={kind} onValueChange={setKind}>
            <SelectTrigger id="src-kind">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SOURCE_KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="src-url">URL (RSS / Atom / scrape — or a YouTube channel URL)</Label>
          <Input
            id="src-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…/feed"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="src-channel">Channel ID (YouTube only — starts with UC)</Label>
          <Input
            id="src-channel"
            value={channelId}
            onChange={(e) => setChannelId(e.target.value)}
            placeholder="UCxxxxxxxxxxxxxxxxxxxxxx"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="src-pattern">Link pattern (html_scrape only — e.g. /p/)</Label>
          <Input
            id="src-pattern"
            value={linkPattern}
            onChange={(e) => setLinkPattern(e.target.value)}
            placeholder="/p/"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="src-group">Group (optional)</Label>
          <Input
            id="src-group"
            value={group}
            onChange={(e) => setGroup(e.target.value)}
            placeholder="substack, newsletter_blog, youtube…"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="src-limit">Limit</Label>
          <Input
            id="src-limit"
            type="number"
            min={1}
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2 pt-6">
          <Switch
            id="src-ondemand"
            checked={onDemand}
            onCheckedChange={setOnDemand}
          />
          <Label htmlFor="src-ondemand" className="font-normal">
            On-demand only
          </Label>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button size="sm" onClick={submit} disabled={add.isPending}>
          {add.isPending ? "Adding…" : "Add Source"}
        </Button>
      </div>
    </div>
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
  // Toggles the inline Add Source form below the header.
  const [showAdd, setShowAdd] = useState(false);

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
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleTestAll}
            disabled={testingAll}
          >
            {testingAll ? "Checking…" : "Test All Sources"}
          </Button>
          <Button size="sm" onClick={() => setShowAdd((v) => !v)}>
            {showAdd ? "Close" : "Add Source"}
          </Button>
        </div>
      </div>

      {/* Inline Add Source form, toggled from the header. */}
      {showAdd && <AddSourceForm onClose={() => setShowAdd(false)} />}

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
