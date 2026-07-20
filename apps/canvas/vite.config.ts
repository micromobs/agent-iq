import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: fileURLToPath(
      new URL("../../.github/extensions/agent-iq-canvas/web", import.meta.url),
    ),
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: "./src/test-setup.ts",
  },
});
