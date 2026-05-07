import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Vite config: dev server proxies /api to the Python dashboard so the React app
// can call the real backend during development without CORS noise.
export default defineConfig({
  plugins: [react()],
  resolve: {
    // Match the "@/*" path alias declared in tsconfig.json so imports stay short.
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      // Forward all /api/* calls to the Python dashboard on its default port.
      "/api": { target: "http://127.0.0.1:8765", changeOrigin: false },
    },
  },
  build: {
    // Emit a stable assets/ subdirectory so dashboard.py can serve it with a
    // small extension allowlist instead of crawling arbitrary output paths.
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
  },
});
