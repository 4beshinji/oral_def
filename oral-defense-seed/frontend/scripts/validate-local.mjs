// Opt-in real-provider validation. Start the app and local LLM before running.
// No provider, TTS, playback-completion, or microphone mocks are installed.
import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = process.env.VALIDATION_ORIGIN || "http://127.0.0.1:8000";
const output = resolve(
  process.env.VALIDATION_OUTPUT || "../../.cache/local-validation/results",
);
const scenarios = (
  process.env.VALIDATION_SCENARIOS || "networking,seminar"
).split(",");
const target = Number(process.env.VALIDATION_TURNS || 6);
const questionKeys = (value) => {
  const normalized = (text) =>
    text
      .normalize("NFKC")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}_]+/gu, " ")
      .trim();
  const sentences = value.split(/[.!?]+/).filter((part) => part.trim());
  const last = sentences.at(-1) || value;
  const questionWord = /\b(?:what|why|how|which|when|where|who)\b/i.exec(last);
  return new Set([
    normalized(value),
    normalized(last),
    ...(questionWord ? [normalized(last.slice(questionWord.index))] : []),
  ]);
};
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
  headless: true,
  // This run checks actual decoding/ended events, not browser autoplay policy.
  args: ["--autoplay-policy=no-user-gesture-required"],
});
const results = [];
try {
  for (const scenario of scenarios) {
    const page = await browser.newPage({
      viewport: { width: 1360, height: 1000 },
    });
    page.setDefaultTimeout(30000);
    const inFlight = new Set();
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        request.url().includes("/conversation/")
      )
        inFlight.add(request);
    });
    page.on("requestfinished", (request) => inFlight.delete(request));
    page.on("requestfailed", (request) => inFlight.delete(request));
    const timings = [];
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("response", async (response) => {
      if (
        response.request().method() === "POST" &&
        response.url().includes("/conversation/")
      ) {
        await response.finished();
        const timing = response.request().timing();
        timings.push({
          path: new URL(response.url()).pathname,
          status: response.status(),
          elapsed_ms: timing.responseEnd,
        });
      }
    });
    await page.goto(origin);
    await page
      .getByLabel("研究概要", { exact: true })
      .fill(
        "This is a synthetic validation case. I am a first-year PhD student studying Bayesian optimization for expensive laboratory experiments. I plan to compare expected improvement with random search on public benchmark functions. I have not run experiments yet and have no measured results, numerical improvements, publications, or proprietary data.",
      );
    await page.getByLabel(/^場面/).selectOption(scenario);
    await page.getByLabel(/^英語の難しさ/).selectOption("simple");
    await page.getByRole("button", { name: "セッションを作成" }).click();
    await page.getByRole("button", { name: "開始 / 再開" }).waitFor();
    const sid = await page.getByLabel("保存したセッション").inputValue();
    await page.getByLabel("会話の速度").selectOption("1.2");
    const started = Date.now();
    let saved,
      last = 0,
      failure = null;
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    while (Date.now() - started < 600000) {
      await page.waitForTimeout(1000);
      saved = await (
        await page.request.get(`${origin}/v1/sessions/${sid}`)
      ).json();
      const confirmed = saved.turns.filter((turn) => turn.confirmed_answer_en);
      if (confirmed.length > last) {
        last = confirmed.length;
        console.log(
          JSON.stringify({
            scenario,
            confirmed: last,
            elapsed_s: (Date.now() - started) / 1000,
            question: confirmed.at(-1).question_en,
            answer: confirmed.at(-1).confirmed_answer_en,
          }),
        );
      }
      const alert = page.locator('[role="alert"]').first();
      if (await alert.isVisible()) {
        failure = await alert.innerText();
        break;
      }
      if (confirmed.length >= target) break;
    }
    const pause = page.getByRole("button", { name: "一時停止", exact: true });
    if (await pause.isEnabled()) await pause.click();
    // Pause invalidates generation immediately; an already-running provider
    // request may still be settling. Let it finish before switching sessions.
    const settleDeadline = Date.now() + 35000;
    while (inFlight.size && Date.now() < settleDeadline)
      await page.waitForTimeout(100);
    if (inFlight.size)
      throw new Error("Previous generation did not settle after pause");
    await page.waitForTimeout(500);
    saved = await (
      await page.request.get(`${origin}/v1/sessions/${sid}`)
    ).json();
    await writeFile(
      `${output}/${scenario}-session.json`,
      JSON.stringify(saved, null, 2),
    );
    await page.screenshot({
      path: `${output}/${scenario}.png`,
      fullPage: true,
    });
    const confirmed = saved.turns.filter((turn) => turn.confirmed_answer_en);
    const seenQuestions = new Set();
    const duplicateQuestionOrdinals = [];
    for (const turn of saved.turns) {
      const keys = questionKeys(turn.question_en);
      if ([...keys].some((key) => seenQuestions.has(key)))
        duplicateQuestionOrdinals.push(turn.ordinal);
      for (const key of keys) seenQuestions.add(key);
    }
    const completedAudio = saved.playbacks.filter(
      (playback) =>
        playback.status === "completed" &&
        playback.audio_id &&
        playback.tts_settings.route === "saved",
    ).length;
    const result = {
      scenario,
      session_id: sid,
      confirmed_turns: confirmed.length,
      elapsed_s: (Date.now() - started) / 1000,
      failure,
      browser_errors: errors,
      timings,
      text_model: saved.settings.text_model,
      speech_models: saved.settings.speech_models,
      completed_audio: completedAudio,
      duplicate_question_ordinals: duplicateQuestionOrdinals,
      recovery_question_ordinals: saved.turns
        .filter((turn) => turn.generation?.recovery)
        .map((turn) => turn.ordinal),
      recorded: saved.turns.some((turn) => turn.has_recording),
      passed:
        !saved.settings.text_model.mock &&
        completedAudio >= 2 * target &&
        !failure &&
        !errors.length &&
        !duplicateQuestionOrdinals.length &&
        confirmed.length >= target &&
        confirmed.every((turn) => turn.submitted_via === "shadowing_playback"),
    };
    results.push(result);
    await writeFile(`${output}/summary.json`, JSON.stringify(results, null, 2));
    console.log(
      JSON.stringify({
        scenario,
        passed: result.passed,
        failure,
        confirmed: confirmed.length,
      }),
    );
    await page.close();
    if (!result.passed)
      throw new Error(failure || "Real-provider conversation did not complete");
  }
} finally {
  await browser.close();
}
