import { useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel,
  AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { useSaveBrain } from "@/lib/queries";
import { pushToast } from "@/hooks/use-toast";

interface AdvancedBrainEditorProps {
  raw: string;
}

// Raw TOML editor with an AlertDialog confirmation guard. Overwriting the brain
// file is the most destructive thing the Settings page can do, so we make
// the user explicitly confirm before posting.
export function AdvancedBrainEditor({ raw }: AdvancedBrainEditorProps) {
  const [value, setValue] = useState(raw);
  const dirty = value !== raw;
  const { mutate: saveBrain, isPending } = useSaveBrain();

  function handleSave() {
    saveBrain(value, {
      onSuccess: () => pushToast({ title: "Brain file saved", description: "Changes take effect on the next run." }),
      onError: (err) => pushToast({ title: "Save failed", description: String(err), variant: "destructive" }),
    });
  }

  return (
    <div className="space-y-4">
      <p className="text-ui text-muted-foreground">
        Edit the raw TOML brain file directly. This overwrites the entire file — use with care.
      </p>

      {/* Monospace textarea with generous height for TOML legibility. */}
      <Textarea
        rows={24}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="font-mono text-xs"
        spellCheck={false}
      />

      <div className="flex items-center gap-3">
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="default" disabled={!dirty || isPending}>
              {isPending ? "Saving…" : "Save brain file"}
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Overwrite brain file?</AlertDialogTitle>
              <AlertDialogDescription>
                This will overwrite the entire agent brain TOML. Invalid TOML will cause the next run to fail.
                Changes take effect on the next agent run.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={handleSave}>Overwrite</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {dirty && <span className="text-meta text-muted-foreground">Unsaved changes</span>}
      </div>
    </div>
  );
}
