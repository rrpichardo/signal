import { Badge } from "@/components/ui/badge";

interface EntitiesPanelProps {
  entities: Record<string, string[] | string>;
}

// Entity chips grouped by category (company, person, product, etc.).
// The entity field is a free-form dict so we normalise values to arrays.
export function EntitiesPanel({ entities }: EntitiesPanelProps) {
  const groups = Object.entries(entities).filter(([, v]) => v && (Array.isArray(v) ? v.length > 0 : v !== ""));
  if (!groups.length) return null;

  return (
    <div className="space-y-3">
      {groups.map(([category, values]) => {
        // Normalise string values to a single-element array.
        const items = Array.isArray(values) ? values : [values];
        return (
          <div key={category} className="flex flex-wrap items-baseline gap-2">
            <span className="kicker mr-1 shrink-0">{category}</span>
            {items.map((item) => (
              <Badge key={item} variant="outline" className="normal-case text-meta">
                {item}
              </Badge>
            ))}
          </div>
        );
      })}
    </div>
  );
}
