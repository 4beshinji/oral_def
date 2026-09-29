export type PendingRecording = {
  key: string;
  sessionId: string;
  exerciseId: string;
  kind: "isolated_repeat" | "shadowing_overlap";
  blob: Blob;
  capture: Record<string, number | boolean>;
  createdAt: number;
};

const DATABASE = "oral-defense-pending-recordings";
const STORE = "recordings";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE, { keyPath: "key" });
    };
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

export async function listPending(sessionId: string) {
  const items = await transaction<PendingRecording[]>("readonly", (store) =>
    store.getAll(),
  );
  return items.filter((item) => item.sessionId === sessionId);
}

export async function prunePendingSessions(activeSessionIds: Set<string>) {
  const items = await transaction<PendingRecording[]>("readonly", (store) =>
    store.getAll(),
  );
  for (const item of items) {
    if (!activeSessionIds.has(item.sessionId)) await removePending(item.key);
  }
}

export async function savePending(item: PendingRecording) {
  await transaction("readwrite", (store) => store.put(item));
}

export async function removePending(key: string) {
  await transaction("readwrite", (store) => store.delete(key));
}

export async function clearSessionPending(sessionId: string) {
  for (const item of await listPending(sessionId))
    await removePending(item.key);
}
