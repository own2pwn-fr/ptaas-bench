import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const root = fileURLToPath(new URL(".", import.meta.url));

/**
 * Client build.
 *
 * The service has no server-side rendering: it hands out one HTML shell and reads the
 * manifest below to learn the hashed names of the bundle and its stylesheet. That is
 * why the manifest is on and why the entry is a module rather than an HTML file.
 */
export default defineConfig({
  root,
  base: "/",
  publicDir: "public",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    manifest: true,
    sourcemap: false,
    rollupOptions: {
      input: "src/main.jsx",
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:3000",
      "/graphql": "http://127.0.0.1:3000",
    },
  },
});
