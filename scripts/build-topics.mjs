// 내장 주제 데이터를 만든다 → extension/data/topics.json, extension/data/topic-vectors.bin, docs/topic-report.md
// 위키백과·Hugging Face 접속이 필요하다. 보통 GitHub Actions 가 실행한다.
//   node scripts/build-topics.mjs

import { mkdir, writeFile } from 'node:fs/promises';
import { pipeline } from '@huggingface/transformers';
import { buildTopicData } from './topic-data.mjs';
import { buildReport } from './topic-report.mjs';
import { decodeVectors } from '../extension/src/lib/quantize.js';

// 확장 프로그램(extension/src/lib/embedder.js)과 반드시 같은 모델·설정이어야 벡터가 서로 비교된다.
const MODEL_ID = 'Xenova/multilingual-e5-small';
const DTYPE = 'q8';
const DIM = 384;

// 위키미디어는 브라우저가 아닌 요청에 연락처가 담긴 User-Agent 를 요구한다
const USER_AGENT = 'slightly-off-recommender-build/0.3 (https://github.com/hallohiyo/chart-finder)';
const fetchImpl = (url, init = {}) => fetch(url, { ...init, headers: { ...init.headers, 'User-Agent': USER_AGENT } });

const log = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);

log(`모델 불러오는 중: ${MODEL_ID} (${DTYPE})`);
const extractor = await pipeline('feature-extraction', MODEL_ID, { dtype: DTYPE });
async function embedTexts(texts) {
  const out = await extractor(texts.map((t) => `query: ${t}`), { pooling: 'mean', normalize: true });
  return texts.map((_, i) => out.data.slice(i * DIM, (i + 1) * DIM));
}

const { meta, topics, vectorBytes } = await buildTopicData({
  fetchImpl,
  embedTexts,
  dim: DIM,
  model: `${MODEL_ID} (${DTYPE})`,
  log,
});

await mkdir('extension/data', { recursive: true });
await writeFile('extension/data/topics.json', JSON.stringify({ meta, topics }));
await writeFile('extension/data/topic-vectors.bin', vectorBytes);
log(`저장: 주제 ${topics.length}개, 벡터 ${(vectorBytes.length / 1e6).toFixed(1)}MB`);

await mkdir('docs', { recursive: true });
// 보고서는 확장 프로그램이 실제로 쓰는 것과 같은 압축 벡터로 만든다
const vectors = decodeVectors(vectorBytes, topics.length, DIM);
await writeFile('docs/topic-report.md', await buildReport({ topics, vectors, dim: DIM, embedTexts, meta }));
log('보고서: docs/topic-report.md');
