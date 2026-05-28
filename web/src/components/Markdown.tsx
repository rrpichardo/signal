import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

// Shared markdown rendering for model-written prose (extended summaries + brief).
// Two variants because bullets need INLINE rendering: dropping a block <p>/<ul>/
// <table> inside an existing <li> produces invalid, ugly nesting.

// External links open in a new tab; styling tracks the editorial accent.
const linkComponent: Components["a"] = ({ href, children }) => (
  <a
    href={href}
    target="_blank"
    rel="noopener noreferrer"
    className="text-accent underline underline-offset-4 decoration-accent/40 hover:decoration-accent"
  >
    {children}
  </a>
);

// Full block rendering: headings, lists, tables, blockquotes, code. Font family
// is inherited from the wrapper's className, so the same component reads as serif
// on the detail page and sans in the brief.
const blockComponents: Components = {
  a: linkComponent,
  p: ({ children }) => <p className="leading-relaxed [&:not(:first-child)]:mt-4">{children}</p>,
  h1: ({ children }) => <h2 className="mt-6 mb-2 text-dek font-semibold">{children}</h2>,
  h2: ({ children }) => <h2 className="mt-6 mb-2 text-dek font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-5 mb-1.5 font-semibold">{children}</h3>,
  h4: ({ children }) => <h4 className="mt-4 mb-1 font-semibold">{children}</h4>,
  ul: ({ children }) => <ul className="list-disc space-y-1 pl-5 [&:not(:first-child)]:mt-4">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5 [&:not(:first-child)]:mt-4">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  blockquote: ({ children }) => (
    <blockquote className="my-4 border-l-4 border-accent pl-4 italic text-foreground/80">{children}</blockquote>
  ),
  hr: () => <hr className="my-6 border-border" />,
  code: ({ children }) => (
    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em]">{children}</code>
  ),
  pre: ({ children }) => (
    <pre className="my-4 overflow-x-auto rounded-md bg-muted p-4 font-mono text-sm [&_code]:bg-transparent [&_code]:p-0">
      {children}
    </pre>
  ),
  // Tables get a horizontal-scroll wrapper so they never overflow on mobile.
  table: ({ children }) => (
    <div className="my-4 overflow-x-auto">
      <table className="w-full border-collapse text-ui">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-border bg-muted/50 px-3 py-2 text-left font-semibold">{children}</th>
  ),
  td: ({ children }) => <td className="border border-border px-3 py-2 align-top">{children}</td>,
};

// Inline-only rendering: keep bold/italic/code/links, but strip block wrappers so
// the output drops straight into an <li> or a chip without nesting block tags.
const inlineComponents: Components = {
  a: linkComponent,
  // Unwrap paragraphs to their inline children (no <p> inside <li>).
  p: ({ children }) => <>{children}</>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  code: ({ children }) => (
    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em]">{children}</code>
  ),
};

// Block markdown — use for expanded_summary, brief summary, section bodies, narrative.
export function MarkdownBlock({ children, className }: { children: string; className?: string }) {
  if (!children) return null;
  return (
    <div className={cn(className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={blockComponents}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

// Inline markdown — use for bullet strings and theme summaries. A stray block
// element degrades to inline text via unwrapDisallowed instead of breaking layout.
export function MarkdownInline({ children, className }: { children: string; className?: string }) {
  if (!children) return null;
  return (
    <span className={cn(className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={inlineComponents}
        disallowedElements={["h1", "h2", "h3", "h4", "h5", "h6", "hr", "table", "thead", "tbody", "tr", "th", "td", "ul", "ol", "li", "blockquote", "pre", "img"]}
        unwrapDisallowed
      >
        {children}
      </ReactMarkdown>
    </span>
  );
}
