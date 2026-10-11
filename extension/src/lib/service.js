// 백그라운드 서비스 로직. chrome API 에 직접 의존하지 않아 테스트에서 가짜 저장소/엔진으로 돌릴 수 있다.

import { buildInterestMap } from './interest-map.js';
import { recommend, applyFeedback } from './recommender.js';
import { fetchVideoMetadata, isValidVideoId } from './youtube.js';
import { localDateKey } from './store.js';
import { youtubeSearchUrl } from './wiki.js';

export const MIN_VIDEOS = 5;

/**
 * @param store   createStore 결과
 * @param engine  createEngine 결과 (loadIndex, ensureIndex, embedVideos ...)
 * @param opts.fetchSummary (koTitle) => Promise<{extract, url}>
 */
export function createService(store, engine, { fetchImpl = fetch, fetchSummary, now = () => Date.now() } = {}) {
  let generating = null;

  function titled(videos) {
    return Object.values(videos).filter((v) => v.title);
  }

  /** 시간이 걸리는 계산(임베딩, 요약 조회)은 잠금 밖에서 하고, 저장만 잠금 안에서 한다 */
  async function generate(dateKey, prefs, recLog) {
    const index = await engine.loadIndex();
    const videos = titled(await store.getVideos());
    const vectors = await engine.embedVideos(videos);
    const map = buildInterestMap(videos, vectors, now());
    const rec = recommend({ interestMap: map, index, prefs, recLog, dateKey });
    if (!rec) return null;
    const summary = fetchSummary ? await fetchSummary(rec.title) : { extract: '', url: '' };
    return {
      ...rec,
      summary: summary.extract || rec.description,
      url: summary.url,
      youtubeUrl: youtubeSearchUrl(rec.title),
    };
  }

  async function view(dateKey, extra = {}) {
    const [settings, daily, videos, indexStatus] = await Promise.all([
      store.getSettings(),
      store.getDaily(),
      store.getVideos(),
      store.getIndexStatus(),
    ]);
    const today = daily?.date === dateKey ? daily : { date: dateKey, recs: [] };
    const current = today.recs.at(-1) ?? null;
    const videoCount = titled(videos).length;
    let status = extra.status;
    if (!status) {
      if (current) status = 'ready';
      else if (videoCount < MIN_VIDEOS) status = 'collecting';
      else status = 'preparing';
    }
    return {
      status,
      recommendation: current,
      index: today.recs.length,
      maxPerDay: settings.maxPerDay,
      canGetMore: Boolean(current?.reaction) && today.recs.length < settings.maxPerDay,
      videoCount,
      minVideos: MIN_VIDEOS,
      hasApiKey: Boolean(settings.apiKey),
      indexStatus,
      error: extra.error,
    };
  }

  async function daily({ generateNext }) {
    const settings = await store.getSettings();
    const dateKey = localDateKey(new Date(now()));
    const daily = await store.getDaily();
    const today = daily?.date === dateKey ? daily : { date: dateKey, recs: [] };
    const current = today.recs.at(-1);
    const wantsNew = !current || (generateNext && current.reaction && today.recs.length < settings.maxPerDay);
    if (!wantsNew) return view(dateKey);

    if (titled(await store.getVideos()).length < MIN_VIDEOS) return view(dateKey, { status: 'collecting' });

    if (!(await engine.loadIndex())) {
      // 주제 목록이 아직 없으면 만들기 시작만 하고 바로 돌아간다 (진행 상황은 indexStatus 로 보여준다)
      engine.ensureIndex().catch(() => {});
      return view(dateKey, { status: 'preparing' });
    }

    try {
      generating ??= (async () => {
        const { recLog, prefs } = await store.updateRecommendationState(() => undefined);
        const rec = await generate(dateKey, prefs, recLog);
        if (!rec) return 'exhausted';
        await store.updateRecommendationState((state) => {
          const latest = state.daily?.date === dateKey ? state.daily : { date: dateKey, recs: [] };
          const last = latest.recs.at(-1);
          // 기다리는 사이에 다른 호출이 이미 추천을 만들었으면 그대로 둔다
          if (last && !last.reaction) return undefined;
          if (latest.recs.length >= settings.maxPerDay) return undefined;
          return {
            daily: { ...latest, recs: [...latest.recs, { ...rec, reaction: null, createdAt: now() }] },
            recLog: [...state.recLog, { topicId: rec.topicId, date: dateKey, rank: rec.rank, reaction: null }],
            prefs: state.prefs,
          };
        });
        return 'ok';
      })().finally(() => {
        generating = null;
      });
      const result = await generating;
      return view(dateKey, result === 'exhausted' ? { status: 'exhausted' } : {});
    } catch (err) {
      return view(dateKey, { status: 'error', error: err.message });
    }
  }

  return {
    async recordWatch({ videoId, title }) {
      const settings = await store.getSettings();
      if (settings.paused || !isValidVideoId(videoId)) return { recorded: false };
      const needsEnrich = await store.recordWatches([{ videoId, title: String(title ?? '').slice(0, 300) }], now());
      return { recorded: true, needsEnrich };
    },

    async importHistory(items) {
      const watches = items.filter((w) => isValidVideoId(w.videoId));
      if (watches.length) await store.recordWatches(watches, now());
      return { imported: watches.length };
    },

    /** API 키가 있으면 메타데이터가 없는 영상들을 조회해 채운다 */
    async enrichPending() {
      const { apiKey } = await store.getSettings();
      if (!apiKey) return { enriched: 0, skipped: 'no-api-key' };
      const ids = await store.pendingEnrichIds(50);
      if (ids.length === 0) return { enriched: 0 };
      const meta = await fetchVideoMetadata(ids, apiKey, fetchImpl);
      await store.applyMetadata(meta, ids);
      return { enriched: meta.size };
    },

    /** 팝업이 열릴 때 호출. 오늘 추천이 없으면 하나 만든다 */
    getToday: () => daily({ generateNext: false }),

    /** 오늘 추천에 반응했고 하루 한도가 남아 있을 때 다음 추천 */
    next: () => daily({ generateNext: true }),

    async react(topicId, reaction) {
      if (reaction !== 'like' && reaction !== 'dislike') throw new Error('invalid reaction');
      const dateKey = localDateKey(new Date(now()));
      await store.updateRecommendationState(({ daily: d, recLog, prefs }) => {
        if (d?.date !== dateKey) return undefined;
        const rec = d.recs.find((r) => r.topicId === topicId);
        if (!rec || rec.reaction) return undefined; // 반응은 한 번만 반영
        return {
          daily: { ...d, recs: d.recs.map((r) => (r === rec ? { ...r, reaction } : r)) },
          recLog: recLog.map((r) => (r.topicId === topicId && r.date === dateKey ? { ...r, reaction } : r)),
          prefs: applyFeedback(prefs, reaction),
        };
      });
      return view(dateKey);
    },

    /** 설정 페이지 요약 */
    async overview() {
      const [prefs, recLog, settings, videos, indexStatus] = await Promise.all([
        store.getPrefs(),
        store.getRecLog(),
        store.getSettings(),
        store.getVideos(),
        store.getIndexStatus(),
      ]);
      const all = Object.values(videos);
      const index = await engine.loadIndex();
      let clusters = [];
      // 모델이 준비된 뒤에만 관심 묶음을 계산한다 (설정 페이지를 열었다고 모델을 받기 시작하지 않도록)
      if (index && all.length) {
        const list = titled(videos);
        const map = buildInterestMap(list, await engine.embedVideos(list), now());
        clusters = map.clusters.map((c) => ({ weight: Math.round(c.weight * 100), size: c.size, examples: c.examples }));
      }
      return {
        settings: { ...settings, apiKey: settings.apiKey ? '••••' + settings.apiKey.slice(-4) : '' },
        videoCount: all.length,
        // 사용자가 실제로 뭐가 저장됐는지 눈으로 확인할 수 있게 최근 영상 일부를 보여준다
        recentVideos: [...all]
          .sort((a, b) => b.lastSeen - a.lastSeen)
          .slice(0, 50)
          .map((v) => ({ id: v.id, title: v.title || '(제목 없음)', lastSeen: v.lastSeen, count: v.count })),
        pendingCount: all.filter((v) => !v.enriched).length,
        topicCount: index?.topics.length ?? 0,
        indexStatus,
        clusters,
        targetRank: prefs.targetRank,
        reactions: {
          like: recLog.filter((r) => r.reaction === 'like').length,
          dislike: recLog.filter((r) => r.reaction === 'dislike').length,
        },
      };
    },
  };
}
