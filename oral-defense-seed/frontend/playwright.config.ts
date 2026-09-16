import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:8000",
    viewport: { width: 1360, height: 1000 },
    launchOptions: {
      executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
      args: [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
      ],
    },
    screenshot: "only-on-failure",
  },
  webServer: {
    command:
      "DATA_DIR=../.cache/e2e-data MODEL_CATALOG_REFRESH_HOURS=0 TEXT_PROVIDER=mock TTS_PROVIDER=mock PRONUNCIATION_PROVIDER=unavailable ../.venv/bin/uvicorn backend.tests.browser_server:app --app-dir .. --host 127.0.0.1 --port 8000",
    url: "http://127.0.0.1:8000/v1/capabilities",
    reuseExistingServer: false,
    timeout: 15000,
  },
});
