// 주제 목록 구축(위키백과) + 벡터 계산(임베딩 모델)을 담당한다.
// 서비스 워커는 언제든 종료될 수 있으므로 진행 상황을 중간중간 저장하고, 다시 호출되면 이어서 한다.

import { fetchVitalTitles, mapToKorean, addKoreanDescriptions, topicText } from './wiki.js';

export const INDEX_VERSION = 1;
const EMBED_BATCH = 32;
const SAVE_EVERY = 256;

/**
 * @param deps.kv          get/set/delete (IndexedDB 또는 테스트용 Map)
 * @param deps.fetchImpl   fetch
 * @param deps.embedTexts  (texts, onModelProgress) => Promise<Float32Array[]>
 * @param deps.dim         벡터 차원
 * @param deps.onStatus    진행 상황 콜백
 */
export function createEngine({ kv, fetchImpl, embedTexts, dim, onStatus = () => {} }) {
  let building = null;
  let cachedIndex = null;

  const report = (status) => onStatus({ ...status, at: Date.now() });

  async function loadIndex() {
    if (cachedIndex) return cachedIndex;
    const meta = await kv.get('indexMeta');
    if (meta?.version !== INDEX_VERSION) return null;
    const [topics, vectors] = await Promise.all([kv.get('topics'), kv.get('topicVectors')]);
    if (!topics || !vectors) return null;
    cachedIndex = { topics, vectors, dim, builtAt: meta.builtAt };
    return cachedIndex;
  }

  async function build() {
    let topics = await kv.get('topics');
    if (!topics) {
      report({ phase: 'listing', done: 0, total: 0 });
      const en = await fetchVitalTitles(fetchImpl, undefined, ({ titles }) =>
        report({ phase: 'listing', done: titles, total: 0 }),
      );
      if (en.length < 100) throw new Error(`위키백과 주제 목록이 너무 적어요 (${en.length}개)`);
      const ko = await mapToKorean(fetchImpl, en, ({ done, total }) => report({ phase: 'translating', done, total }));
      topics = await addKoreanDescriptions(fetchImpl, ko, ({ done, total }) => report({ phase: 'describing', done, total }));
      await kv.set('topics', topics);
      await kv.delete('topicVectorsPartial');
    }

    const partial = (await kv.get('topicVectorsPartial')) ?? { count: 0, data: new Float32Array(topics.length * dim) };
    let lastSaved = partial.count;
    report({ phase: 'embedding', done: partial.count, total: topics.length });
    for (let i = partial.count; i < topics.length; i += EMBED_BATCH) {
      const batch = topics.slice(i, i + EMBED_BATCH);
      const vecs = await embedTexts(batch.map(topicText), ({ loaded, total }) =>
        report({ phase: 'downloading', done: loaded, total }),
      );
      vecs.forEach((v, j) => partial.data.set(v, (i + j) * dim));
      partial.count = i + batch.length;
      if (partial.count - lastSaved >= SAVE_EVERY || partial.count === topics.length) {
        await kv.set('topicVectorsPartial', partial);
        lastSaved = partial.count;
      }
      report({ phase: 'embedding', done: partial.count, total: topics.length });
    }

    await kv.set('topicVectors', partial.data);
    await kv.set('indexMeta', { version: INDEX_VERSION, builtAt: Date.now(), count: topics.length });
    await kv.delete('topicVectorsPartial');
    cachedIndex = null;
    const index = await loadIndex();
    report({ phase: 'ready', done: topics.length, total: topics.length });
    return index;
  }

  return {
    loadIndex,

    /** 인덱스가 있으면 바로 돌려주고, 없으면 (이어서) 만든다. 동시에 여러 번 불려도 한 번만 돈다 */
    ensureIndex() {
      if (!building) {
        building = loadIndex()
          .then((index) => index ?? build())
          .catch((err) => {
            report({ phase: 'error', error: err.message });
            throw err;
          })
          .finally(() => {
            building = null;
          });
      }
      return building;
    },

    isBuilding: () => building !== null,

    /** 주제 목록을 처음부터 다시 받는다 */
    async resetIndex() {
      cachedIndex = null;
      for (const key of ['topics', 'topicVectors', 'topicVectorsPartial', 'indexMeta']) await kv.delete(key);
      report({ phase: 'idle' });
    },

    /** 영상 벡터를 계산한다. 이미 계산한 영상은 캐시에서 꺼낸다. @returns Map<videoId, Float32Array> */
    async embedVideos(videos) {
      const cache = (await kv.get('videoVectors')) ?? {};
      const next = {};
      const missing = [];
      for (const v of videos) {
        const text = videoText(v);
        if (!text) continue;
        if (cache[v.id]?.text === text) next[v.id] = cache[v.id];
        else missing.push({ id: v.id, text });
      }
      for (let i = 0; i < missing.length; i += EMBED_BATCH) {
        const batch = missing.slice(i, i + EMBED_BATCH);
        const vecs = await embedTexts(batch.map((m) => m.text));
        batch.forEach((m, j) => {
          next[m.id] = { text: m.text, vec: vecs[j] };
        });
      }
      // 지워진 영상의 벡터는 함께 버린다
      await kv.set('videoVectors', next);
      return new Map(Object.entries(next).map(([id, e]) => [id, e.vec]));
    },

    async clearVideoVectors() {
      await kv.delete('videoVectors');
    },
  };
}

export function videoText(video) {
  const title = (video.title ?? '').trim();
  if (!title) return '';
  const tags = (video.tags ?? []).slice(0, 8);
  return tags.length ? `${title} / ${tags.join(', ')}` : title;
}
