export async function api<T>(
  path: string,
  data?: unknown,
  method = "POST",
): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 65000);
  try {
    const response = await fetch("/v1" + path, {
      method: data === undefined && method === "POST" ? "GET" : method,
      headers:
        data instanceof FormData || data === undefined
          ? undefined
          : { "Content-Type": "application/json" },
      body:
        data === undefined
          ? undefined
          : data instanceof FormData
            ? data
            : JSON.stringify(data),
      signal: controller.signal,
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(
        result.message || `リクエストに失敗しました (${response.status})`,
      );
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw new Error(
        "時間切れです。再読込で保存状態を確認してください。外部APIの再試行は再課金される場合があります。",
      );
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export const requestId = () => ({ request_id: crypto.randomUUID() });
