import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./test/browser",
  timeout: 30000,
  use: {
    baseURL: "http://localhost:5180",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node server.mjs",
    url: "http://localhost:5180",
    reuseExistingServer: !process.env.CI,
  },
});
