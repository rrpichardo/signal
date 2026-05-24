// Helpers for reading/patching nested config objects by a dotted manifest id
// (e.g. "behavior.scout_mode", "scoring.value_weights.novelty").
//
// Patches carry the FULL top-level section so they merge correctly with the
// shallow draft accumulator in SettingsPage (each save replaces a whole
// top-level key like `behavior` / `scoring` / `display`).

type Obj = Record<string, unknown>;

export function getByPath(source: unknown, dottedId: string): unknown {
  let cur: unknown = source;
  for (const part of dottedId.split(".")) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Obj)[part];
  }
  return cur;
}

// Returns a patch object whose single top-level key is the section, with the
// nested value updated (cloning along the path so we never mutate state).
export function patchByPath(source: unknown, dottedId: string, value: unknown): Obj {
  const parts = dottedId.split(".");
  const top = parts[0];
  const base = (source && typeof source === "object" ? (source as Obj) : {}) as Obj;

  // Single-level key, e.g. "behavior.scout_mode" within a section object passed
  // as `source`. We always rebuild from the section root `top`.
  const rootValue = base[top];
  const cloned = deepSet(rootValue, parts.slice(1), value);
  return { [top]: cloned };
}

function deepSet(node: unknown, parts: string[], value: unknown): unknown {
  if (parts.length === 0) return value;
  const obj = (node && typeof node === "object" ? { ...(node as Obj) } : {}) as Obj;
  obj[parts[0]] = deepSet(obj[parts[0]], parts.slice(1), value);
  return obj;
}
