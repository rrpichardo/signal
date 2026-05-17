import { NavLink, Link } from "react-router-dom";
import { ThemeToggle } from "./ThemeToggle";
import { RunStatusPill } from "./RunStatusPill";
import { longDate } from "@/lib/format";
import { cn } from "@/lib/utils";

// Editorial masthead. Top row: wordmark + date + nav + status + theme toggle.
// Bottom row: hairline rule that anchors the rest of the page.
export function Masthead() {
  // Today's date is rendered with the long editorial format (e.g. "Sunday, May 4 2026").
  const today = longDate(new Date().toISOString());

  return (
    <header className="border-b border-border bg-background">
      <div className="container max-w-6xl">
        {/* Top row — wordmark + date + utilities. */}
        <div className="flex items-center justify-between py-5">
          <Link to="/" className="flex items-baseline gap-3">
            <span className="font-serif text-h2 font-semibold tracking-tight">Signal Stream</span>
            <span className="hidden text-meta text-muted-foreground sm:inline">{today}</span>
          </Link>

          <div className="flex items-center gap-3">
            <RunStatusPill />
            <ThemeToggle />
          </div>
        </div>

        {/* Section nav — underline-on-active style, like printed-paper section heads. */}
        <nav className="-mb-px flex items-center gap-6 overflow-x-auto pb-0">
          {[
            { to: "/", label: "Digest", end: true },
            { to: "/activity", label: "Activity" },
            { to: "/memory", label: "Memory" },
            { to: "/sources", label: "Sources" },
            { to: "/settings", label: "Settings" },
          ].map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                cn(
                  "border-b-2 border-transparent pb-3 pt-1 text-ui font-medium text-muted-foreground transition-colors hover:text-foreground",
                  isActive && "border-accent text-foreground",
                )
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}
