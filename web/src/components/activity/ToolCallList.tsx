import { useState } from "react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import { shortTime, tryParse } from "@/lib/format";
import type { ToolCall } from "@/lib/types";

// Tool call table. Row click opens a Side Sheet with the full JSON payload so
// the table stays scannable and detail is opt-in.
export function ToolCallList({ calls }: { calls: ToolCall[] }) {
  const [selected, setSelected] = useState<ToolCall | null>(null);

  if (!calls.length) {
    return <p className="text-meta text-muted-foreground">No tool calls recorded for this run.</p>;
  }

  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Agent</TableHead>
            <TableHead>Tool</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="hidden sm:table-cell">Confidence</TableHead>
            <TableHead className="hidden md:table-cell">Time</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {calls.map((call) => (
            <TableRow
              key={call.id}
              className="cursor-pointer"
              onClick={() => setSelected(call)}
            >
              <TableCell className="font-medium capitalize">{call.agent}</TableCell>
              <TableCell className="font-mono text-xs">{call.tool}</TableCell>
              <TableCell>
                <Badge
                  variant={
                    call.status === "success" ? "low" :
                    call.status === "error" ? "critical" : "default"
                  }
                >
                  {call.status}
                </Badge>
              </TableCell>
              <TableCell className="hidden text-muted-foreground sm:table-cell">
                {call.confidence !== null ? `${Math.round((call.confidence ?? 0) * 100)}%` : "—"}
              </TableCell>
              <TableCell className="hidden font-mono text-xs text-muted-foreground md:table-cell">
                {shortTime(call.created_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {/* JSON inspector sheet — shows input/output for the clicked row. */}
      <Sheet open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent className="flex flex-col gap-4">
          {selected && (
            <>
              <SheetHeader>
                <SheetTitle className="font-mono text-ui">{selected.tool}</SheetTitle>
                <SheetDescription>
                  {selected.agent} · {shortTime(selected.created_at)}
                  {selected.error && (
                    <span className="ml-2 text-destructive">{selected.error}</span>
                  )}
                </SheetDescription>
              </SheetHeader>

              <ScrollArea className="flex-1">
                <div className="space-y-4">
                  <div>
                    <div className="kicker mb-2">Input</div>
                    <pre className="overflow-x-auto rounded-sm border border-border bg-muted p-3 font-mono text-xs">
                      {JSON.stringify(tryParse(selected.input_json) ?? selected.input_json, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <div className="kicker mb-2">Output</div>
                    <pre className="overflow-x-auto rounded-sm border border-border bg-muted p-3 font-mono text-xs">
                      {JSON.stringify(tryParse(selected.output_json) ?? selected.output_json, null, 2)}
                    </pre>
                  </div>
                </div>
              </ScrollArea>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
