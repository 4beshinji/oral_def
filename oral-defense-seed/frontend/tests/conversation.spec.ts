import { expect, test, type Page } from "@playwright/test";

async function browserVoice(
  page: Page,
  behavior: "normal" | "blocked" | "held",
) {
  await page.addInitScript((behavior) => {
    Object.defineProperty(window.speechSynthesis, "speak", {
      value: (utterance: SpeechSynthesisUtterance) => {
        Object.assign(window, { testUtterance: utterance });
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

test("free speech manual fallback commits and progresses without pronunciation score", async ({
  page,
  request,
}) => {
  await browserVoice(page, "normal");
  const sid = await create(page);
  try {
    await page.getByLabel("現在の会話モード").selectOption("free_speech");
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    await expect(page.getByLabel("自由発話の代替入力")).toBeVisible();
    await page
      .getByLabel("自由発話の代替入力")
      .fill("I have not run that experiment.");
    await page.getByRole("button", { name: "入力文を返答として確定" }).click();
    await expect
      .poll(async () => {
        const saved = await (await request.get(`/v1/sessions/${sid}`)).json();
        return saved.turns[0]?.submitted_via;
      })
      .toBe("free_speech_manual");
    await expect(page.getByLabel("自由発話の代替入力")).toBeVisible();
    const saved = await (await request.get(`/v1/sessions/${sid}`)).json();
    expect(saved.turns[0].confirmed_answer_en).toBe(
      "I have not run that experiment.",
    );
    expect(saved.turns[0].transcript).toBeNull();
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("free speech recording remains available after ASR failure and reload", async ({
  page,
  request,
}) => {
  await browserVoice(page, "normal");
  const sid = await create(page);
  try {
    await page.getByLabel("現在の会話モード").selectOption("free_speech");
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    await expect(
      page.getByRole("button", { name: "マイクで録音" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "マイクで録音" }).click();
    await page.waitForTimeout(800);
    await page.getByRole("button", { name: "録音を停止" }).click();
    await expect(
      page.getByRole("button", { name: "この録音を認識して返答" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "この録音を認識して返答" }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "モデル" }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByRole("button", { name: "この録音を認識して返答" }),
    ).toBeVisible();
    const saved = await (await request.get(`/v1/sessions/${sid}`)).json();
    expect(saved.turns[0].confirmed_answer_en).toBeNull();
    expect(saved.turns[0].has_recording).toBe(true);
    await page
      .getByLabel("自由発話の代替入力")
      .fill("I will check the evidence.");
    await page.getByRole("button", { name: "入力文を返答として確定" }).click();
    await expect
      .poll(async () => {
        const current = await (await request.get(`/v1/sessions/${sid}`)).json();
        return current.turns[0]?.submitted_via;
      })
      .toBe("free_speech_manual");
    await page.getByText(/確定済み公開会話/).click();
    await expect(page.getByText("自由発話の手入力")).toBeVisible();
    await page
      .getByRole("button", { name: "参照文・録音・評価を表示" })
      .first()
      .click();
    await expect(page.getByText(/自由発話の録音 ·/)).toBeVisible();
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("prepare a brief and pin its source before conversation", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByLabel("資料を準備してから会話を開始").check();
  await page.getByRole("button", { name: "セッションを作成" }).click();
  await expect
    .poll(() => page.getByLabel("保存したセッション").inputValue())
    .not.toBe("");
  const sid = await page.getByLabel("保存したセッション").inputValue();
  try {
    await expect(
      page.getByRole("heading", { name: "会話に使う資料を選ぶ" }),
    ).toBeVisible();
    await page.getByLabel("資料の位置づけ").selectOption("learner_work");
    await page
      .getByLabel("研究概要・メモ")
      .fill("My measured result is still unknown.");
    await page.getByRole("button", { name: "メモを追加" }).click();
    await expect(
      page.getByText("My measured result is still unknown."),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "資料を確定して会話画面へ" })
      .click();
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
    const saved = (await (await request.get(`/v1/sessions/${sid}`)).json()) as {
      pack_snapshot: { source_material: { role: string; text: string }[] };
      pack_manifest: { pack_hash: string };
      pack_hash: string;
    };
    expect(saved.pack_manifest.pack_hash).toBe(saved.pack_hash);
    expect(saved.pack_snapshot.source_material[0]).toMatchObject({
      role: "learner_work",
      text: "My measured result is still unknown.",
    });
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

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
      .getByRole("button", { name: "返答をブラウザで読み上げ" })
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

test("Coach support remains private and adopts a saved answer only while paused", async ({
  page,
  request,
}) => {
  await browserVoice(page, "held");
  const sid = await create(page);
  const get = async () => (await request.get(`/v1/sessions/${sid}`)).json();
  try {
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    await expect
      .poll(async () => (await get()).conversation.playback_id)
      .not.toBeNull();
    await page.evaluate(() => {
      const utterance = (
        window as Window & { testUtterance: SpeechSynthesisUtterance }
      ).testUtterance;
      utterance.dispatchEvent(new Event("end"));
    });
    await expect
      .poll(async () => (await get()).conversation.reference_id)
      .not.toBeNull();
    const panel = page.getByRole("region", { name: "会話中のCoach支援" });
    await panel.getByLabel("会話Coachへのメモ").fill("PRIVATE_COACH_BROWSER");
    await panel.getByRole("button", { name: "ヒント", exact: true }).click();
    await expect(panel.getByTestId("coach-support")).toHaveCount(2);
    expect((await get()).turns[0].confirmed_answer_en).toBeNull();
    await panel
      .getByLabel("会話Coachの下書き")
      .fill("I have not collected data yet.");
    await panel.getByRole("button", { name: "文章修正", exact: true }).click();
    await expect(panel.getByTestId("coach-support")).toHaveCount(3);
    const revision = panel.getByTestId("coach-support").last();
    await expect(
      revision.getByRole("button", { name: "このCoach案を採用" }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "一時停止", exact: true }).click();
    await expect(
      revision.getByRole("button", { name: "このCoach案を採用" }),
    ).toBeEnabled();
    const before = await get();
    await revision.getByRole("button", { name: "このCoach案を採用" }).click();
    await expect
      .poll(async () => (await get()).conversation.reference_id)
      .not.toBe(before.conversation.reference_id);
    const adopted = await get();
    expect(adopted.turns[0].confirmed_answer_en).toBeNull();
    expect(adopted.turns[0].exercises.at(-1).reference_text).toBe(
      "I have not collected data yet.",
    );
    expect(
      await (await request.get(`/v1/sessions/${sid}/export`)).text(),
    ).not.toContain("PRIVATE_COACH_BROWSER");
    await page.reload();
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
    await expect(page.getByTestId("coach-support")).toHaveCount(3);
    await page
      .getByRole("region", { name: "会話中のCoach支援" })
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: "test-results/conversation-coach.png" });
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("isolated recording survives upload failure and reload", async ({
  page,
  request,
}) => {
  await browserVoice(page, "held");
  const sid = await create(page);
  const get = async () => (await request.get(`/v1/sessions/${sid}`)).json();
  try {
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    await expect
      .poll(async () => (await get()).conversation.playback_id)
      .not.toBeNull();
    await page.evaluate(() => {
      const utterance = (
        window as Window & { testUtterance: SpeechSynthesisUtterance }
      ).testUtterance;
      utterance.dispatchEvent(new Event("end"));
    });
    await expect
      .poll(async () => (await get()).conversation.reference_id)
      .not.toBeNull();
    const exerciseId = (await get()).conversation.reference_id as string;
    await page.getByRole("button", { name: "一時停止", exact: true }).click();
    const recorder = page.getByRole("region", { name: "会話の録音" });
    await page.route("**/v1/exercises/*/audio", (route) => route.abort());
    await recorder.getByRole("button", { name: "単独復唱を録音" }).click();
    await page.waitForTimeout(700);
    await recorder.getByRole("button", { name: "録音を停止して保存" }).click();
    await expect(
      recorder.getByRole("heading", { name: "保存待ち録音 (1)" }),
    ).toBeVisible();
    expect((await get()).turns[0].exercises[0].attempts_total).toBe(0);
    await page.reload();
    await expect(
      recorder.getByRole("heading", { name: "保存待ち録音 (1)" }),
    ).toBeVisible();
    await page.unroute("**/v1/exercises/*/audio");
    await recorder.getByRole("button", { name: "再送" }).click();
    await expect(
      recorder.getByRole("heading", { name: "保存待ち録音 (1)" }),
    ).toHaveCount(0);
    const saved = await get();
    expect(saved.turns[0].exercises[0].attempts_total).toBe(1);
    expect(saved.turns[0].exercises[0].attempts[0].input_kind).toBe(
      "isolated_repeat",
    );
    expect(saved.turns[0].exercises[0].id).toBe(exerciseId);
    expect(saved.turns[0].confirmed_answer_en).toBeNull();
    await page.getByRole("button", { name: "音響評価を実行" }).click();
    await expect(page.getByTestId("assessment-run")).toContainText(
      "評価器が未設定です",
    );
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("overlap recording closes before the next question and is marked insufficient", async ({
  page,
  request,
}) => {
  await browserVoice(page, "held");
  const sid = await create(page);
  const get = async () => (await request.get(`/v1/sessions/${sid}`)).json();
  try {
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    await expect
      .poll(async () => (await get()).conversation.playback_id)
      .not.toBeNull();
    await page.evaluate(() => {
      (
        window as Window & { testUtterance: SpeechSynthesisUtterance }
      ).testUtterance.dispatchEvent(new Event("end"));
    });
    await expect
      .poll(async () => (await get()).conversation.reference_id)
      .not.toBeNull();
    const recorder = page.getByRole("region", { name: "会話の録音" });
    await recorder.getByRole("button", { name: "お手本と同時録音" }).click();
    await expect(
      recorder.getByRole("button", { name: "録音を停止して保存" }),
    ).toBeVisible();
    await page.waitForTimeout(700);
    await page.evaluate(() => {
      (
        window as Window & { testUtterance: SpeechSynthesisUtterance }
      ).testUtterance.dispatchEvent(new Event("end"));
    });
    await expect
      .poll(async () => (await get()).turns[0].exercises[0].attempts_total)
      .toBe(1);
    const saved = await get();
    expect(saved.turns[0].confirmed_answer_en).not.toBeNull();
    expect(saved.turns[0].exercises[0].attempts[0].input_kind).toBe(
      "shadowing_overlap",
    );
    const attemptId = saved.turns[0].exercises[0].attempts[0].id as string;
    const assessed = await request.post(`/v1/attempts/${attemptId}/assess`, {
      data: { request_id: crypto.randomUUID() },
    });
    expect(assessed.ok()).toBe(true);
    const runs = (await (
      await request.get(`/v1/attempts/${attemptId}/assessments`)
    ).json()) as {
      items: { evidence_status: string }[];
    };
    expect(runs.items[0].evidence_status).toBe("insufficient_evidence");
    await page.getByRole("button", { name: "一時停止", exact: true }).click();
    await page.screenshot({ path: "test-results/conversation-recording.png" });
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});

test("a delayed Coach reply is labelled with its original turn while playback continues", async ({
  page,
  request,
}) => {
  await browserVoice(page, "normal");
  const sid = await create(page);
  let deliver: (() => void) | undefined;
  const held = new Promise<void>((resolve) => {
    deliver = resolve;
  });
  await page.route("**/v1/turns/*/coach", async (route) => {
    const response = await route.fetch();
    await held;
    await route.fulfill({ response });
  });
  try {
    await page.getByRole("button", { name: "開始 / 再開" }).click();
    const panel = page.getByRole("region", { name: "会話中のCoach支援" });
    await panel.getByRole("button", { name: "ヒント", exact: true }).click();
    await expect(panel.getByRole("status")).toContainText("支援を生成");
    await expect
      .poll(
        async () =>
          (await (await request.get(`/v1/sessions/${sid}`)).json())
            .confirmed_turns_total,
      )
      .toBeGreaterThanOrEqual(4);
    await page.getByRole("button", { name: "一時停止", exact: true }).click();
    deliver!();
    await expect(panel.getByLabel("以前の会話への支援")).toBeVisible();
    await expect(panel.getByLabel("以前の会話への支援")).toContainText(
      "元の会話の履歴",
    );
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
  } finally {
    deliver!();
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

test("51 turns retain paged references, audio, and assessment history across reload and new turns", async ({
  page,
  request,
}) => {
  const sid = await create(page);
  try {
    const fixture = await request.post(`/v1/test/history/${sid}`, { data: {} });
    expect(fixture.ok(), await fixture.text()).toBe(true);
    await page.reload();
    await expect(
      page.getByRole("button", { name: "開始 / 再開" }),
    ).toBeEnabled();
    await page.getByText(/^確定済み公開会話/).click();
    await expect(page.getByText("履歴取得済み 50 / 全51ターン")).toBeVisible();
    await page.getByRole("button", { name: "以前の会話を読み込む" }).click();
    await expect(page.getByTestId("history-turn")).toHaveCount(51);
    await expect(page.getByText("履歴取得済み 51 / 全51ターン")).toBeVisible();

    // A new turn changes the latest 50 window. Restore the earlier loaded range.
    expect(
      (await request.post(`/v1/test/history/${sid}/append`, { data: {} })).ok(),
    ).toBe(true);
    await page.reload();
    await expect(page.getByTestId("history-turn")).toHaveCount(52);
    const ordinals = await page
      .getByTestId("history-turn")
      .evaluateAll((elements) =>
        elements.map((element) => Number(element.getAttribute("data-ordinal"))),
      );
    expect(ordinals).toEqual(
      Array.from({ length: 52 }, (_, index) => index + 1),
    );
    const oldest = page.getByTestId("history-turn").first();
    await expect(oldest).toContainText("録音: あり");
    await oldest
      .getByRole("button", { name: "参照文・録音・評価を表示" })
      .click();
    await expect(oldest.getByText("参照文 20 / 23件")).toBeVisible();
    await oldest
      .getByRole("button", { name: "以前の参照文を読み込む" })
      .click();
    await expect(oldest.getByText("参照文 23 / 23件")).toBeVisible();
    await oldest.getByRole("button", { name: "以前の試行を読み込む" }).click();
    await expect(oldest.getByText("試行 23 / 23件")).toBeVisible();
    const recording = oldest.getByTestId("history-attempt").first();
    await recording
      .locator("audio")
      .evaluate(async (audio: HTMLAudioElement) => {
        await audio.play();
      });
    await recording.getByRole("button", { name: "評価履歴を表示" }).click();
    await expect(recording.getByText("評価履歴 20 / 23件")).toBeVisible();
    await recording
      .getByRole("button", { name: "以前の評価を読み込む" })
      .click();
    await expect(recording.getByTestId("assessment-run")).toHaveCount(23);
    await expect(recording).toContainText("fixture_0");
    const saved = await (await request.get(`/v1/sessions/${sid}`)).json();
    expect(saved.confirmed_turns_total).toBe(52);
    expect(saved.conversation.status).toBe("paused");
    await oldest.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: "test-results/conversation-history.png",
      fullPage: false,
    });
  } finally {
    await request.delete(`/v1/sessions/${sid}`);
  }
});
