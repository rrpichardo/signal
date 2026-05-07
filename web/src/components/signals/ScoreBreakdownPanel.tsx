import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ScorePill } from "./ScorePill";
import type { ScoreBreakdownItem } from "@/lib/types";

interface ScoreBreakdownPanelProps {
  score: number;
  breakdown: ScoreBreakdownItem[];
}

// Collapsed behind an Accordion so it doesn't visually compete with the article
// body. A reader who wants the scoring rationale can expand it; otherwise the
// detail page stays editorially clean.
export function ScoreBreakdownPanel({ score, breakdown }: ScoreBreakdownPanelProps) {
  if (!breakdown.length) return null;

  return (
    <Card>
      <Accordion type="single" collapsible>
        <AccordionItem value="breakdown" className="border-b-0">
          <CardHeader className="pb-0">
            <AccordionTrigger className="py-0 hover:no-underline">
              <div className="flex items-center gap-3">
                <CardTitle>Score breakdown</CardTitle>
                <ScorePill score={score} />
              </div>
            </AccordionTrigger>
          </CardHeader>

          <AccordionContent>
            <CardContent className="pt-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Factor</TableHead>
                    <TableHead className="w-20 text-right">Points</TableHead>
                    <TableHead>Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {breakdown.map((item, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-medium">{item.name ?? "—"}</TableCell>
                      <TableCell className="text-right font-mono">
                        {item.points !== undefined ? `+${item.points}` : "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground">{item.reason ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </Card>
  );
}
