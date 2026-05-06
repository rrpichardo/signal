import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

// 404 page — minimal, editorial tone.
export default function NotFoundPage() {
  return (
    <div className="py-32 text-center">
      <p className="kicker mb-4">404</p>
      <h1 className="font-serif text-display font-semibold">Page not found</h1>
      <p className="mt-4 text-body text-muted-foreground">That page doesn't exist.</p>
      <Link to="/" className="mt-8 inline-flex items-center gap-2 text-accent hover:underline underline-offset-4">
        <ArrowLeft className="h-4 w-4" /> Back to the digest
      </Link>
    </div>
  );
}
