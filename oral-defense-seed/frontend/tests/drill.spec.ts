import { test, expect, Page } from "@playwright/test";

test("select a supplied model, reload, and change it without losing the session", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page
    .getByLabel("対話相手のモデル", { exact: true })
    .selectOption("opencode-go/browser-fixture-one");
  await page
    .getByLabel("文章修正のモデル", { exact: true })
    .selectOption("opencode-go/browser-fixture-two");
  await page
    .getByLabel("会話モード", { exact: true })
    .selectOption("independent");
  await page.getByRole("button", { name: "セッションを作成" }).click();
  await expect(
    page.getByRole("button", { name: "最初の質問を生成" }),
  ).toBeVisible();
  const sid = await page.getByLabel("保存したセッション").inputValue();
  try {
    await page.reload();
    await expect(
      page.getByLabel("対話相手のモデル", { exact: true }),
    ).toHaveValue("opencode-go/browser-fixture-one");
    await page
      .getByLabel("対話相手のモデル", { exact: true })
      .selectOption("opencode-go/browser-fixture-two");
    await expect
      .poll(
        async () =>
          (await (await request.get("/v1/sessions/" + sid)).json()).settings
            .text_model.id,
      )
      .toBe("opencode-go/browser-fixture-two");
    await page.reload();
    await expect(
      page.getByLabel("対話相手のモデル", { exact: true }),
    ).toHaveValue("opencode-go/browser-fixture-two");
    await expect(
      page.getByLabel("文章修正のモデル", { exact: true }),
    ).toHaveValue("opencode-go/browser-fixture-two");
    for (const name of [
      "対話手本（全文案）",
      "質問の意味",
      "ヒント",
      "回答の骨子",
    ]) {
      await expect(
        page.getByLabel(`${name}のモデル`, { exact: true }),
      ).toHaveValue("mock/demo");
    }
    await page
      .getByLabel("文章修正のモデル", { exact: true })
      .selectOption("mock/demo");
    await expect
      .poll(
        async () =>
          (await (await request.get("/v1/sessions/" + sid)).json()).settings
            .role_models.revision.id,
      )
      .toBe("mock/demo");
    await page
      .getByLabel("対話相手のモデル", { exact: true })
      .selectOption("mock/demo");
    await expect
      .poll(
        async () =>
          (await (await request.get("/v1/sessions/" + sid)).json()).settings
            .text_model.id,
      )
      .toBe("mock/demo");
    await page.getByRole("button", { name: "最初の質問を生成" }).click();
    await expect(
      page.getByRole("heading", { name: "Question 01" }),
    ).toBeVisible();
  } finally {
    await request.delete("/v1/sessions/" + sid);
  }
});

test("model choices refresh automatically when due and again after 24 hours", async ({
  page,
  request,
}) => {
  const catalog = await (await request.get("/v1/text-models")).json();
  let calls = 0;
  const epoch = Date.now();
  await page.clock.install({ time: new Date(epoch) });
  await page.route("**/v1/text-models", (route) =>
    route.fulfill({
      json: { ...catalog, auto_refresh_hours: 24, next_refresh_at: 0 },
    }),
  );
  await page.route("**/v1/text-models/refresh", async (route) => {
    expect(route.request().postDataJSON().force).toBe(false);
    calls += 1;
    await route.fulfill({
      json: {
        ...catalog,
        auto_refresh_hours: 24,
        next_refresh_at: (epoch + calls * 86400000) / 1000,
      },
    });
  });
  await page.goto("/");
  await expect(
    page.getByLabel("対話相手のモデル", { exact: true }),
  ).toBeVisible();
  await page.clock.runFor(1500);
  await expect.poll(() => calls).toBe(1);
  await page.clock.runFor(60000);
  expect(calls).toBe(1);
  await page.clock.fastForward(86400000);
  await expect.poll(() => calls).toBe(2);
  await expect(
    page.getByLabel("対話相手のモデル", { exact: true }),
  ).toHaveValue("mock/demo");
});

async function start(page: Page) {
  await page.goto("/");
  await page
    .getByLabel("研究概要", { exact: true })
    .fill("ブラウザ自動検証用の架空研究。実際の研究成果ではありません。");
  await page
    .getByLabel("会話モード", { exact: true })
    .selectOption("independent");
  await page.getByRole("button", { name: "セッションを作成" }).click();
  await page.getByRole("button", { name: "最初の質問を生成" }).click();
  await expect(
    page.getByRole("heading", { name: "Question 01" }),
  ).toBeVisible();
  return await page.getByLabel("保存したセッション").inputValue();
}

test("three questions, private coach, fixed text, dictation, persistence and deletion", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const sid = await start(page);
  try {
    await page
      .getByRole("button", { name: "質問文を表示", exact: true })
      .click();
    await expect(
      page.getByText("What problem does your research address?", {
        exact: true,
      }),
    ).toBeVisible();
    await page.getByLabel("日本語メモ").fill("PRIVATE_BROWSER_SENTINEL");
    await page.getByRole("button", { name: "全文案", exact: true }).click();
    await page.getByRole("button", { name: "回答欄に取り込む" }).click();
    await expect(page.getByLabel("回答文", { exact: true })).toHaveValue(
      /I am still clarifying/,
    );
    await page
      .getByLabel("回答文", { exact: true })
      .fill("I compare against random search.");
    await page
      .getByLabel("練習モード", { exact: true })
      .selectOption("dictation");
    await page
      .getByRole("button", { name: "この文で練習", exact: true })
      .click();
    await expect(page.getByTestId("reference")).not.toContainText(
      "I compare against random search.",
    );
    await page
      .getByLabel("聞こえた英文")
      .fill("i compare against random search!");
    await page.getByRole("button", { name: "単語の差分を確認" }).click();
    await expect(
      page.getByText("正規化後の単語が一致しました。"),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByText("正規化後の単語が一致しました。"),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "この内容で回答した", exact: true })
      .click();
    await page.getByRole("button", { name: "次の質問へ" }).click();
    for (const n of [2, 3]) {
      await expect(
        page.getByRole("heading", { name: `Question 0${n}` }),
      ).toBeVisible();
      await page
        .getByLabel("回答文", { exact: true })
        .fill(`I need to verify claim ${n}.`);
      await page
        .getByRole("button", { name: "この文で練習", exact: true })
        .click();
      await page
        .getByRole("button", { name: "この内容で回答した", exact: true })
        .click();
      await expect(
        page.getByText("回答を確定しました。", { exact: true }),
      ).toBeVisible();
      if (n < 3) await page.getByRole("button", { name: "次の質問へ" }).click();
    }
    await page.reload();
    await expect(page.getByLabel("回答文", { exact: true })).toHaveValue(
      "I need to verify claim 3.",
    );
    const result = await request.get(`/v1/sessions/${sid}/export`);
    const exported = await result.json();
    expect(exported.session.turns).toHaveLength(3);
    expect(
      exported.session.turns.every(
        (t: { confirmed_answer_en: string | null }) =>
          t.confirmed_answer_en !== null,
      ),
    ).toBeTruthy();
    expect(
      exported.session.turns[0].assistance.some(
        (a: { kind: string }) => a.kind === "coach_full_answer",
      ),
    ).toBeTruthy();
    expect(errors).toEqual([]);
    await page.screenshot({
      path: "test-results/practice-desktop.png",
      fullPage: true,
    });
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "削除", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "今回、何を話しますか。" }),
    ).toBeVisible();
    expect((await request.get(`/v1/sessions/${sid}`)).status()).toBe(404);
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("MediaRecorder saves separate attempts and keeps unavailable assessment playable", async ({
  page,
  request,
}) => {
  const sid = await start(page);
  try {
    await page
      .getByLabel("回答文", { exact: true })
      .fill("This is a synthetic browser recording test.");
    await page
      .getByRole("button", { name: "この文で練習", exact: true })
      .click();
    for (const n of [1, 2]) {
      await page
        .getByRole("button", { name: "録音を開始", exact: true })
        .click();
      await expect(page.getByText(/録音中 1 \/ 30秒/)).toBeVisible();
      await page
        .getByRole("button", { name: "停止して保存", exact: true })
        .click();
      await expect(page.locator(".attempt")).toHaveCount(n);
    }
    await page
      .getByRole("button", { name: "音響評価を実行", exact: true })
      .first()
      .click();
    await expect(
      page.getByText(
        "発音評価は利用できません。録音の再生と反復は利用できます。",
        { exact: true },
      ),
    ).toBeVisible();
    await page
      .locator(".attempt audio")
      .first()
      .evaluate(async (audio: HTMLAudioElement) => {
        await audio.play();
      });
    const session = await (await request.get(`/v1/sessions/${sid}`)).json();
    const attempts = session.turns[0].exercises[0].attempts;
    expect(attempts[0].audio_id).not.toBe(attempts[1].audio_id);
    expect(
      (await request.get(`/v1/audio/${attempts[0].audio_id}`)).status(),
    ).toBe(200);
    await page.reload();
    await expect(page.locator(".attempt")).toHaveCount(2);
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("microphone denial and TTS unavailable preserve editable answer", async ({
  page,
  request,
}) => {
  const sid = await start(page);
  try {
    await page
      .getByLabel("回答文", { exact: true })
      .fill("My editable answer.");
    await page
      .getByRole("button", { name: "この文で練習", exact: true })
      .click();
    await page.evaluate(() => {
      navigator.mediaDevices.getUserMedia = async () => {
        throw new DOMException("Denied", "NotAllowedError");
      };
    });
    await page.getByRole("button", { name: "録音を開始", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText(
      "マイクの使用が許可されませんでした",
    );
    await expect(page.getByLabel("回答文", { exact: true })).toBeEnabled();
    await page
      .getByRole("button", { name: "基準音声を再生", exact: true })
      .click();
    await expect(page.getByRole("alert")).toContainText("保存TTSは未設定");
    await expect(page.getByLabel("回答文", { exact: true })).toHaveValue(
      "My editable answer.",
    );
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("speech roles can be selected separately and survive reload", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page
    .getByLabel("対話相手の音声", { exact: true })
    .selectOption("kokoro/sky-anime");
  await page
    .getByLabel("回答案のお手本の音声", { exact: true })
    .selectOption("piper/ljspeech");
  await page
    .getByLabel("練習用のお手本の音声", { exact: true })
    .selectOption("piper/ljspeech");
  await page
    .getByLabel("会話モード", { exact: true })
    .selectOption("independent");
  await page.getByRole("button", { name: "セッションを作成" }).click();
  await expect(
    page.getByRole("button", { name: "最初の質問を生成" }),
  ).toBeVisible();
  const sid = await page.getByLabel("保存したセッション").inputValue();
  try {
    await page.reload();
    await expect(
      page.getByLabel("対話相手の音声", { exact: true }),
    ).toHaveValue("kokoro/sky-anime");
    await page
      .getByLabel("対話相手の音声", { exact: true })
      .selectOption("kokoro/heart");
    await expect
      .poll(
        async () =>
          (await (await request.get(`/v1/sessions/${sid}`)).json()).settings
            .speech_models.question,
      )
      .toBe("kokoro/heart");
    await page.reload();
    await expect(
      page.getByLabel("対話相手の音声", { exact: true }),
    ).toHaveValue("kokoro/heart");
    await expect(
      page.getByLabel("回答案のお手本の音声", { exact: true }),
    ).toHaveValue("piper/ljspeech");
    await expect(
      page.getByLabel("練習用のお手本の音声", { exact: true }),
    ).toHaveValue("piper/ljspeech");
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});
