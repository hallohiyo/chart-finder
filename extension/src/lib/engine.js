// 내장 주제 데이터(extension/data/)를 읽고, 영상 벡터를 계산한다.
// 주제 목록과 주제 벡터는 미리 만들어 확장 프로그램에 들어 있다 (scripts/build-topics.mjs).
// 실행 중에 필요한 건 영상 제목을 분석할 모델뿐이라, 처음 한 번 모델만 내려받는다.

import { prepareIndex, toTopicSpace } from './topic-space.js';
import { dot } from './vector.js';

const EMBED_BATCH = 32;

/**
 * @param deps.kv          get/set/delete (IndexedDB 또는 테스트용 Map) — 영상 벡터 캐시용
 * @param deps.loadBundled () => Promise<{meta, topics, vectors}> 내장 데이터 읽기
 * @param deps.embedTexts  (texts, onModelProgress) => Promise<Float32Array[]>
 * @param deps.onStatus    준비 상황 콜백 ({phase: 'downloading'|'ready'|'error', ...})
 */
export function createEngine({ kv, loadBundled, embedTexts, onStatus = () => {} }) {
  let indexPromise = null;
  let warming = null;
  let modelReady = false;

  const report = (status) => onStatus({ ...status, at: Date.now() });

  function loadIndex() {
    indexPromise ??= loadBundled()
      .then(prepareIndex)
      .catch((err) => {
        indexPromise = null;
        throw err;
      });
    return indexPromise;
  }

  return {
    loadIndex,

    isModelReady: () => modelReady,

    /**
     * 모델을 불러온다. 처음엔 내려받기(약 120MB), 그다음부턴 브라우저 캐시에서 읽는다.
     * 동시에 여러 번 불려도 한 번만 돈다.
     */
    warmUp() {
      if (modelReady) return Promise.resolve();
      warming ??= (async () => {
        report({ phase: 'downloading', done: 0, total: 0 });
        const index = await loadIndex();
        // 자가 점검: 내장 데이터를 만들 때와 같은 문장을 넣어 같은 벡터가 나오는지 본다.
        // 다르면 내 영상과 주제가 서로 다른 공간에 있어서 추천이 엉뚱해진다.
        const probes = index.meta.probes ?? [];
        const texts = probes.length ? probes.map((p) => p.text) : ['준비'];
        const vecs = await embedTexts(texts, ({ loaded, total }) => report({ phase: 'downloading', done: loaded, total }));
        const check = probes.length ? Math.min(...probes.map((p, i) => dot(vecs[i], p.vec))) : null;
        modelReady = true;
        report({ phase: 'ready', done: index.topics.length, total: index.topics.length, check });
      })()
        .catch((err) => {
          report({ phase: 'error', error: err.message });
          throw err;
        })
        .finally(() => {
          warming = null;
        });
      return warming;
    },

    /**
     * 영상 벡터를 계산한다. 이미 계산한 영상은 캐시에서 꺼낸다.
     * 캐시에는 모델 출력 그대로 두고, 돌려줄 때 주제 공간(중심화)으로 옮긴다.
     * @returns Map<videoId, Float32Array>
     */
    async embedVideos(videos) {
      const index = await loadIndex();
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
      return new Map(Object.entries(next).map(([id, e]) => [id, toTopicSpace(e.vec, index)]));
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
