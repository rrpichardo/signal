import { Button } from "@/components/ui/button";

interface SaveBarProps {
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
}

// Sticky bottom bar that appears when form values diverge from the saved state.
// Gives a clear "something is unsaved" signal without blocking the UI.
export function SaveBar({ dirty, saving, onSave, onDiscard }: SaveBarProps) {
  if (!dirty) return null;

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 border-t border-border bg-card/95 backdrop-blur">
      <div className="container max-w-6xl flex items-center justify-between py-3">
        <span className="text-ui text-muted-foreground">You have unsaved changes</span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onDiscard} disabled={saving}>
            Discard
          </Button>
          <Button variant="default" size="sm" onClick={onSave} disabled={saving}>
            {saving ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </div>
    </div>
  );
}
