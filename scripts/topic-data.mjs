// 내장 주제 데이터 만들기: 위키백과 Vital articles → 한국어 문서 → 설명 → 임베딩 → int8 압축.
// 네트워크(위키백과·Hugging Face)가 필요해서 GitHub Actions 에서 돈다 (.github/workflows/build-topics.yml).

import { fetchVitalTitles, mapToKorean, addKoreanDescriptions, topicText } from '../extension/src/lib/wiki.js';
import { quantize, encodeVectors } from '../extension/src/lib/quantize.js';

export const DATA_VERSION = 1;
const EMBED_BATCH = 64;

/**
 * @returns {{ meta, topics, vectorBytes, vectors }} meta/topics 는 topics.json, vectorBytes 는 topic-vectors.bin
 */
export async function buildTopicData({ fetchImpl, embedTexts, dim, model, log = () => {} }) {
  const { titles, diagnostics } = await fetchVitalTitles(fetchImpl, ({ titles: n }) => {
    if (n % 1000 < 50) log(`  목록: ${n}개`);
  });
  log(`영어 문서 ${titles.length}개 (${diagnostics})`);
  if (titles.length < 100) throw new Error(`위키백과 주제 목록이 너무 적어요 (${diagnostics})`);

  const ko = await mapToKorean(fetchImpl, titles, ({ done, total }) => {
    if (done % 1000 < 50) log(`  한국어 연결: ${done}/${total}`);
  });
  log(`한국어 문서가 있는 주제 ${ko.length}개`);
  const topics = await addKoreanDescriptions(fetchImpl, ko);
  log(`설명이 있는 주제 ${topics.filter((t) => t.description).length}개`);

  const vectors = new Float32Array(topics.length * dim);
  for (let i = 0; i < topics.length; i += EMBED_BATCH) {
    const batch = topics.slice(i, i + EMBED_BATCH);
    const vecs = await embedTexts(batch.map(topicText));
    vecs.forEach((v, j) => vectors.set(v, (i + j) * dim));
    if ((i / EMBED_BATCH) % 20 === 0) log(`  임베딩: ${i + batch.length}/${topics.length}`);
  }

  const meta = {
    version: DATA_VERSION,
    builtAt: new Date().toISOString(),
    source: 'English Wikipedia Vital articles Level 4 (People/Geography excluded) → Korean Wikipedia',
    diagnostics,
    model,
    dim,
    count: topics.length,
    quantization: 'int8, per-vector scale',
  };
  return { meta, topics, vectors, vectorBytes: encodeVectors(quantize(vectors, dim)) };
}
