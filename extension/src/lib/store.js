// 확장 프로그램 상태. 모든 데이터는 chrome.storage.local 에만 저장된다 (동기화/서버 전송 없음).
// storage 인자는 chrome.storage.local 과 같은 get/set/remove/clear 인터페이스를 가진 객체.

import { DEFAULT_PREFS } from './recommender.js';

export const DEFAULT_SETTINGS = Object.freeze({
  apiKey: '',
  paused: false,
  maxPerDay: 1,
});

const MAX_VIDEOS = 1000;
const RETENTION_DAYS = 180;
const RECOUNT_GAP_MS = 30 * 60 * 1000; // 같은 영상을 30분 안에 다시 열면 재시청으로 세지 않음
const MAX_REC_LOG = 365;

export function createStore(storage) {
  // 서비스 워커 안에서 메시지가 동시에 처리돼도 읽기-수정-쓰기가 겹치지 않도록 직렬화한다.
  let queue = Promise.resolve();
  function exclusive(fn) {
    const run = queue.then(fn, fn);
    queue = run.catch(() => {});
    return run;
  }
  async function get(key, fallback) {
    const res = await storage.get(key);
    return res[key] ?? fallback;
  }
  async function set(key, value) {
    await storage.set({ [key]: value });
  }
  async function getSettings() {
    return { ...DEFAULT_SETTINGS, ...(await get('settings', {})) };
  }

  return {
    getSettings,
    updateSettings: (patch) =>
      exclusive(async () => {
        const next = { ...(await getSettings()), ...patch };
        next.maxPerDay = Math.min(3, Math.max(1, Math.round(Number(next.maxPerDay) || 1)));
        next.apiKey = String(next.apiKey ?? '').trim();
        next.paused = Boolean(next.paused);
        await set('settings', next);
        return next;
      }),

    getVideos: () => get('videos', {}),
    /** 시청 기록들을 반영한다. 새로 메타데이터 조회가 필요한 영상이 있으면 true */
    recordWatches: (watches, now = Date.now()) =>
      exclusive(async () => {
        const videos = await get('videos', {});
        let needsEnrich = false;
        for (const w of watches) needsEnrich = upsertVideo(videos, w, w.at ?? now) || needsEnrich;
        await set('videos', pruneVideos(videos, now));
        return needsEnrich;
      }),
    applyMetadata: (metaById, attemptedIds) =>
      exclusive(async () => {
        const videos = await get('videos', {});
        for (const id of attemptedIds) {
          const v = videos[id];
          if (!v) continue;
          const meta = metaById.get(id);
          if (meta) Object.assign(v, meta, { title: meta.title || v.title });
          // 삭제/비공개 영상은 다시 조회하지 않도록 표시만 해둔다
          v.enriched = true;
        }
        await set('videos', videos);
      }),
    pendingEnrichIds: async (limit = 50) =>
      Object.values(await get('videos', {}))
        .filter((v) => !v.enriched)
        .sort((a, b) => b.lastSeen - a.lastSeen)
        .slice(0, limit)
        .map((v) => v.id),

    getPrefs: async () => ({ ...DEFAULT_PREFS, ...(await get('prefs', {})) }),
    getRecLog: () => get('recLog', []),
    getDaily: () => get('daily', null),

    /** 오늘의 추천 상태를 원자적으로 갱신한다. fn(state) → 새 state 또는 undefined(변경 없음) */
    updateRecommendationState: (fn) =>
      exclusive(async () => {
        const state = {
          daily: await get('daily', null),
          recLog: await get('recLog', []),
          prefs: { ...DEFAULT_PREFS, ...(await get('prefs', {})) },
        };
        const next = await fn(state);
        if (next) {
          await storage.set({ daily: next.daily, recLog: next.recLog.slice(-MAX_REC_LOG), prefs: next.prefs });
        }
        return next ?? state;
      }),

    getIndexStatus: () => get('indexStatus', { phase: 'idle' }),
    setIndexStatus: (status) => set('indexStatus', status),

    /** 설정(API 키 등)은 남기고 수집/추천 데이터만 지운다 */
    clearData: () => exclusive(() => storage.remove(['videos', 'prefs', 'recLog', 'daily'])),
  };
}

/** videos 객체를 제자리에서 갱신. 메타데이터 조회가 필요한 새 영상이면 true */
export function upsertVideo(videos, { videoId, title }, now) {
  const existing = videos[videoId];
  if (existing) {
    if (now - existing.lastSeen > RECOUNT_GAP_MS) existing.count += 1;
    existing.lastSeen = now;
    if (!existing.title && title) existing.title = title;
    return false;
  }
  videos[videoId] = {
    id: videoId,
    title: title ?? '',
    description: '',
    tags: [],
    categoryId: null,
    firstSeen: now,
    lastSeen: now,
    count: 1,
    enriched: false,
  };
  return true;
}

/** 보관 기간이 지난 영상을 지우고, 최신순으로 MAX_VIDEOS개만 남긴다 */
export function pruneVideos(videos, now) {
  const cutoff = now - RETENTION_DAYS * 86400000;
  const kept = Object.values(videos)
    .filter((v) => v.lastSeen >= cutoff)
    .sort((a, b) => b.lastSeen - a.lastSeen)
    .slice(0, MAX_VIDEOS);
  return Object.fromEntries(kept.map((v) => [v.id, v]));
}

/** 로컬 시간 기준 YYYY-MM-DD */
export function localDateKey(date = new Date()) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
