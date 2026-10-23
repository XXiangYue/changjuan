import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  outputDir: "../.cache/browser-tests",
  use: {
    baseURL: "http://127.0.0.1:18765",
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "pnpm exec vite --host 127.0.0.1 --port 18765 --strictPort",
    url: "http://127.0.0.1:18765",
  },
});
