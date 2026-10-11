// 백그라운드 서비스 로직. chrome API 에 직접 의존하지 않아 테스트에서 가짜 저장소로 돌릴 수 있다.

import { buildInterestMap } from './interest-map.js';
import { recommend, applyFeedback } from './recommender.js';
import { fetchVideoMetadata, isValidVideoId } from './youtube.js';
import { localDateKey } from './store.js';
import { DOMAINS } from '../data/domains.js';

export const MIN_VIDEOS = 5;
const DOMAIN_LABELS = Object.fromEntries(DOMAINS.map((d) => [d.id, d.label]));

export function createService(store, { fetchImpl = fetch, now = () => Date.now() } = {}) {
  async function interestMap() {
    return buildInterestMap(Object.values(await store.getVideos()), now());
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
    async getToday() {
      return this._daily({ generateNext: false });
    },

    /** 오늘 추천에 모두 반응했고 하루 한도가 남아 있을 때 다음 추천 */
    async next() {
      return this._daily({ generateNext: true });
    },

    async _daily({ generateNext }) {
      const settings = await store.getSettings();
      const dateKey = localDateKey(new Date(now()));
      const map = await interestMap();
      let status = null;

      const state = await store.updateRecommendationState(({ daily, recLog, prefs }) => {
        const today = daily?.date === dateKey ? daily : { date: dateKey, recs: [] };
        const current = today.recs.at(-1);
        const canAddMore = today.recs.length < settings.maxPerDay;
        const shouldGenerate = !current || (generateNext && current.reaction && canAddMore);
        if (!shouldGenerate) return today === daily ? undefined : { daily: today, recLog, prefs };

        if (map.videoCount < MIN_VIDEOS) {
          status = 'collecting';
          return today === daily ? undefined : { daily: today, recLog, prefs };
        }
        const rec = recommend({ interestMap: map, prefs, recLog, dateKey });
        if (!rec) {
          status = 'exhausted';
          return undefined;
        }
        const entry = { ...rec, reaction: null, createdAt: now() };
        return {
          daily: { ...today, recs: [...today.recs, entry] },
          recLog: [...recLog, { topicId: rec.topicId, date: dateKey, distance: rec.distance, reaction: null }],
          prefs,
        };
      });

      const today = state.daily?.date === dateKey ? state.daily : { date: dateKey, recs: [] };
      const current = today.recs.at(-1) ?? null;
      return {
        status: status ?? (current ? 'ready' : 'collecting'),
        recommendation: current,
        index: today.recs.length,
        maxPerDay: settings.maxPerDay,
        canGetMore: Boolean(current?.reaction) && today.recs.length < settings.maxPerDay,
        videoCount: map.videoCount,
        minVideos: MIN_VIDEOS,
        hasApiKey: Boolean(settings.apiKey),
      };
    },

    async react(topicId, reaction) {
      if (reaction !== 'like' && reaction !== 'dislike') throw new Error('invalid reaction');
      const dateKey = localDateKey(new Date(now()));
      await store.updateRecommendationState(({ daily, recLog, prefs }) => {
        if (daily?.date !== dateKey) return undefined;
        const rec = daily.recs.find((r) => r.topicId === topicId);
        if (!rec || rec.reaction) return undefined; // 반응은 한 번만 반영
        return {
          daily: { ...daily, recs: daily.recs.map((r) => (r === rec ? { ...r, reaction } : r)) },
          recLog: recLog.map((r) => (r.topicId === topicId && r.date === dateKey ? { ...r, reaction } : r)),
          prefs: applyFeedback(prefs, rec, reaction),
        };
      });
      return this.getToday();
    },

    /** 설정 페이지의 "내 관심 지도" 보기용 요약 */
    async overview() {
      const [map, prefs, recLog, settings, videos] = await Promise.all([
        interestMap(),
        store.getPrefs(),
        store.getRecLog(),
        store.getSettings(),
        store.getVideos(),
      ]);
      const all = Object.values(videos);
      return {
        settings: { ...settings, apiKey: settings.apiKey ? '••••' + settings.apiKey.slice(-4) : '' },
        videoCount: all.length,
        pendingCount: all.filter((v) => !v.enriched).length,
        clusters: map.clusters.map((c) => ({
          weight: Math.round(c.weight * 100),
          size: c.size,
          domains: c.topDomains.map(([id]) => DOMAIN_LABELS[id]),
          representative: c.representative.title,
        })),
        targetDistance: prefs.targetDistance,
        reactions: {
          like: recLog.filter((r) => r.reaction === 'like').length,
          dislike: recLog.filter((r) => r.reaction === 'dislike').length,
        },
      };
    },
  };
}
