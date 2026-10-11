// 아주 작은 IndexedDB key-value 래퍼. 주제 목록과 벡터(수 MB)는 chrome.storage 대신 여기에 둔다.

const DB_NAME = 'slightly-off';
const STORE = 'kv';

function open() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

let dbPromise = null;

async function tx(mode, fn) {
  dbPromise ??= open();
  const db = await dbPromise;
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(req?.result);
    t.onerror = () => reject(t.error);
  });
}

export const idbKv = {
  get: (key) => tx('readonly', (s) => s.get(key)),
  set: (key, value) => tx('readwrite', (s) => s.put(value, key)),
  delete: (key) => tx('readwrite', (s) => s.delete(key)),
};
