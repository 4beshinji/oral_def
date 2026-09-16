import { expect, test, type Page } from "@playwright/test";

async function browserVoice(
  page: Page,
  behavior: "normal" | "blocked" | "held",
) {
  await page.addInitScript((behavior) => {
    Object.defineProperty(window.speechSynthesis, "speak", {
      value: (utterance: SpeechSynthesisUtterance) => {
        if (behavior === "normal")
          setTimeout(
            () => utterance.onend?.(new Event("end") as SpeechSynthesisEvent),
            100,
          );
        if (behavior === "blocked")
          setTimeout(
            () =>
              utterance.onerror?.(
                new Event("error") as SpeechSynthesisErrorEvent,
              ),
            10,
          );
      },
    });
    Object.defineProperty(window.speechSynthesis, "cancel", {
      value: () => {},
    });
  }, behavior);
}

async function create(page: Page) {
  await page.goto("/");
  await expect(page.getByLabel("会話モード", { exact: true })).toHaveValue(
    "shadowing",
  );
  await page.getByRole("button", { name: "セッションを作成" }).click();
  await expect(page.getByRole("button", { name: "開始 / 再開" })).toBeEnabled();
  await page.getByLabel("会話の音声経路").selectOption("browser");
  return page.getByLabel("保存したセッション").inputValue();
}

test("automatic seven exchanges without microphone, pause, reload, and repeat", async ({
  page,
  request,
}) => {
  await browserVoice(page, "normal");
  const sid = await create(page);
  const get = async () => (await request.get(`/v1/sessions/${sid}`)).json();
  try {
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    await expect
      .poll(
        async () =>
          (await get()).turns.filter(
            (turn: { confirmed_answer_en: string | null }) =>
              turn.confirmed_answer_en,
          ).length,
      )
      .toBeGreaterThanOrEqual(7);
    await page.getByRole("button", { name: "一時停止", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
    const stopped = await get();
    expect(stopped.conversation.status).toBe("paused");
    expect(
      stopped.turns.every(
        (turn: { has_recording: boolean }) => !turn.has_recording,
      ),
    ).toBe(true);
    await page.reload();
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
    expect((await get()).turns).toEqual(stopped.turns);
    await page.getByText(/^確定済み公開会話/).click();
    await page
      .getByRole("button", { name: "過去のお手本をブラウザで反復" })
      .first()
      .click();
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
    expect((await get()).turns).toEqual(stopped.turns);
    await page.getByRole("button", { name: "会話を終了", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "会話を終了しました" }),
    ).toBeVisible();
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("rejected playback remains unconfirmed and can be retried", async ({
  page,
  request,
}) => {
  await browserVoice(page, "blocked");
  const sid = await create(page);
  try {
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    await expect(page.getByRole("alert")).toContainText(
      "ブラウザ読み上げに失敗",
    );
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
    let saved = await (await request.get(`/v1/sessions/${sid}`)).json();
    expect(saved.turns).toHaveLength(1);
    expect(saved.turns[0].confirmed_answer_en).toBeNull();
    expect(saved.conversation.status).toBe("paused");
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
    saved = await (await request.get(`/v1/sessions/${sid}`)).json();
    expect(saved.turns).toHaveLength(1);
    expect(saved.turns[0].confirmed_answer_en).toBeNull();
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("reload during playback cancels pending completion", async ({
  page,
  request,
}) => {
  await browserVoice(page, "held");
  const sid = await create(page);
  try {
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/v1/sessions/${sid}`)).json()).conversation
            .playback_id,
      )
      .not.toBeNull();
    const saved = await (await request.get(`/v1/sessions/${sid}`)).json();
    await page.reload();
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
    const late = await request.post(`/v1/sessions/${sid}/conversation/ended`, {
      data: {
        request_id: crypto.randomUUID(),
        playback_id: saved.conversation.playback_id,
      },
    });
    expect(late.status()).toBe(409);
    const reloaded = await (await request.get(`/v1/sessions/${sid}`)).json();
    expect(reloaded.conversation.status).toBe("paused");
    expect(reloaded.turns[0].confirmed_answer_en).toBeNull();
    await page.getByLabel("保存したセッション").selectOption("");
    await expect(page.getByLabel("会話モード", { exact: true })).toHaveValue(
      "shadowing",
    );
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});
