export type PendingFreeSpeech = {
  key: string;
  sessionId: string;
  turnId: string;
  blob: Blob;
  capture: Record<string, number | boolean>;
  createdAt: number;
};

const DATABASE = "oral-defense-pending-free-speech";
const STORE = "recordings";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () =>
      request.result.createObjectStore(STORE, { keyPath: "key" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function transaction<T>(
  mode: IDBTransactionMode,
  operate: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const database = await open();
  return new Promise((resolve, reject) => {
    const tx = database.transaction(STORE, mode);
    const request = operate(tx.objectStore(STORE));
    let result: T;
    request.onsuccess = () => {
      result = request.result;
    };
    tx.oncomplete = () => {
      database.close();
      resolve(result);
    };
    tx.onerror = () => {
      database.close();
      reject(tx.error);
    };
    tx.onabort = tx.onerror;
  });
}

export async function listPendingFreeSpeech(
  sessionId: string,
): Promise<PendingFreeSpeech[]> {
  const items = await transaction<PendingFreeSpeech[]>("readonly", (store) =>
    store.getAll(),
  );
  return items.filter((item) => item.sessionId === sessionId);
}

export async function savePendingFreeSpeech(item: PendingFreeSpeech) {
  await transaction("readwrite", (store) => store.put(item));
}

export async function removePendingFreeSpeech(key: string) {
  await transaction("readwrite", (store) => store.delete(key));
}

export async function clearSessionPendingFreeSpeech(sessionId: string) {
  for (const item of await listPendingFreeSpeech(sessionId))
    await removePendingFreeSpeech(item.key);
}

export async function prunePendingFreeSpeech(activeSessionIds: Set<string>) {
  const items = await transaction<PendingFreeSpeech[]>("readonly", (store) =>
    store.getAll(),
  );
  for (const item of items)
    if (!activeSessionIds.has(item.sessionId))
      await removePendingFreeSpeech(item.key);
}
