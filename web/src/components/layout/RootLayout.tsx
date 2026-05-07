import { Outlet } from "react-router-dom";
import { Masthead } from "./Masthead";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ToastProvider, ToastViewport, Toast, ToastTitle, ToastDescription, ToastClose } from "@/components/ui/toast";
import { useToasts, dismissToast } from "@/hooks/use-toast";

// Top-level shell: masthead + page content + footer + toast layer.
// Toast viewport lives here so save toasts from any page show up consistently.
export default function RootLayout() {
  const toasts = useToasts();

  return (
    <TooltipProvider>
      <ToastProvider>
        <div className="min-h-screen bg-background text-foreground">
          <Masthead />
          <main className="container max-w-6xl py-10">
            <Outlet />
          </main>
          <footer className="container max-w-6xl border-t border-border py-6 text-meta text-muted-foreground">
            Signal Stream · local-first AI/tech intelligence
          </footer>
        </div>

        {/* Render any active toasts produced by useToasts() / pushToast(). */}
        {toasts.map((t) => (
          <Toast
            key={t.id}
            onOpenChange={(open) => {
              if (!open) dismissToast(t.id);
            }}
            className={t.variant === "destructive" ? "border-destructive/50" : undefined}
          >
            <div className="flex flex-col gap-0.5">
              <ToastTitle>{t.title}</ToastTitle>
              {t.description && <ToastDescription>{t.description}</ToastDescription>}
            </div>
            <ToastClose />
          </Toast>
        ))}
        <ToastViewport />
      </ToastProvider>
    </TooltipProvider>
  );
}
