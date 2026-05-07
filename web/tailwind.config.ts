import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

// Tailwind config bound to the CSS-variable design tokens defined in src/index.css.
// All semantic colors resolve through HSL vars so the same utility classes work in
// light and dark themes without conditional class wiring at the call site.
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1.5rem" },
    extend: {
      colors: {
        // Map every Tailwind color name to a CSS variable so theme switches are
        // a single class change on <html>, not a re-render of utility classes.
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        // Editorial urgency tokens, used by UrgencyBadge and ScorePill.
        urgency: {
          critical: "hsl(var(--urgency-critical))",
          high: "hsl(var(--urgency-high))",
          medium: "hsl(var(--urgency-medium))",
          low: "hsl(var(--urgency-low))",
        },
      },
      borderRadius: {
        // Editorial radius is small on purpose; bubbly corners read as web-app, not brief.
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        // Source Serif 4 carries the editorial register; Inter handles UI chrome;
        // JetBrains Mono is reserved for TOML and JSON payloads.
        serif: ["'Source Serif 4'", "'Source Serif Pro'", "Georgia", "serif"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      fontSize: {
        // Editorial type scale; values come straight from the approved plan.
        kicker: ["0.75rem", { lineHeight: "1", letterSpacing: "0.12em" }],
        meta: ["0.8125rem", { lineHeight: "1.4" }],
        ui: ["0.875rem", { lineHeight: "1.45" }],
        dek: ["1.125rem", { lineHeight: "1.5" }],
        body: ["1.0625rem", { lineHeight: "1.65" }],
        h3: ["1.25rem", { lineHeight: "1.3" }],
        h2: ["1.75rem", { lineHeight: "1.2" }],
        h1: ["2.25rem", { lineHeight: "1.15" }],
        display: ["3rem", { lineHeight: "1.1" }],
      },
      keyframes: {
        // Used by Radix-driven components (accordion, dialog) via tailwindcss-animate.
        "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
        "accordion-up": { from: { height: "var(--radix-accordion-content-height)" }, to: { height: "0" } },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [animate],
} satisfies Config;
