import { useState } from "react";
import { useMemory } from "@/lib/queries";
import { MemoryCard } from "@/components/memory/MemoryCard";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

// Memory page — browse all stored memory items, filterable by topic chip.
export default function MemoryPage() {
  const { data: items = [], isLoading } = useMemory();
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null);

  // Derive unique topics from the memory list for filter chips.
  const topics = [...new Set(items.map((i) => i.topic))].sort();

  const filtered = selectedTopic
    ? items.filter((i) => i.topic === selectedTopic)
    : items;

  if (isLoading) {
    return (
      <div className="space-y-5">
        <Skeleton className="h-8 w-72" />
        {[1, 2, 3].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
      </div>
    );
  }

  return (
    <div>
      <div className="kicker mb-6">Memory — {items.length} items</div>

      {/* Topic filter chips — clicking one limits the list; clicking again clears. */}
      {topics.length > 1 && (
        <div className="mb-8 flex flex-wrap gap-2">
          {topics.map((t) => (
            <button key={t} onClick={() => setSelectedTopic(t === selectedTopic ? null : t)}>
              <Badge
                variant={t === selectedTopic ? "accent" : "outline"}
                className="cursor-pointer normal-case text-meta"
              >
                {t.replace(/_/g, " ")}
              </Badge>
            </button>
          ))}
        </div>
      )}

      {filtered.length === 0 ? (
        <p className="py-12 text-center text-body text-muted-foreground">No memory items yet.</p>
      ) : (
        <div>
          {filtered.map((item) => (
            <MemoryCard key={item.id} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}
