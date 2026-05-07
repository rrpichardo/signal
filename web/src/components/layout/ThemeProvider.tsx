import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ReactNode } from "react";

// next-themes handles the localStorage persistence and the prefers-color-scheme
// media query. attribute="class" toggles `.dark` on <html>, which is what
// Tailwind's darkMode: ["class"] config expects.
export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      {children}
    </NextThemesProvider>
  );
}
