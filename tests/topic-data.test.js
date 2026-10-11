import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTopicData } from '../scripts/topic-data.mjs';
import { buildReport } from '../scripts/topic-report.mjs';
import { quantize, encodeVectors, decodeVectors } from '../extension/src/lib/quantize.js';
import { loadBundledTopics } from '../extension/src/lib/bundled-data.js';
import { dot } from '../extension/src/lib/vector.js';
import { fakeEmbed, fakeWikiFetch, FAKE_DIM } from './helpers.js';

const ROOT = 'Wikipedia:Vital articles/Level/4';

function wiki(n = 200) {
  const links = Array.from({ length: n }, (_, i) => ({ ns: 0, title: `Topic ${i}` }));
  const langlinks = Object.fromEntries(links.map((l, i) => [l.title, `주제${i}`]));
  const descriptions = Object.fromEntries(links.map((_, i) => [`주제${i}`, `음악 과학 ${i % 7}`]));
  return fakeWikiFetch({ vital: { [ROOT]: [{ ns: 4, title: `${ROOT}/Arts` }], [`${ROOT}/Arts`]: links }, langlinks, descriptions });
}

test('압축(int8)해도 벡터 방향은 거의 그대로다', async () => {
  const vecs = await fakeEmbed(['재즈 피아노', '양자 물리', '김치찌개 레시피']);
  const flat = new Float32Array(vecs.length * FAKE_DIM);
  vecs.forEach((v, i) => flat.set(v, i * FAKE_DIM));
  const bytes = encodeVectors(quantize(flat, FAKE_DIM));
  assert.equal(bytes.length, 3 * 4 + 3 * FAKE_DIM);
  const back = decodeVectors(bytes, 3, FAKE_DIM);
  for (let i = 0; i < 3; i++) {
    const sim = dot(vecs[i], back, i * FAKE_DIM);
    assert.ok(sim > 0.999, `cos ${sim}`);
  }
  assert.throws(() => decodeVectors(bytes.subarray(1), 3, FAKE_DIM), /크기가 맞지 않아요/);
});

test('내장 데이터 만들기 → 파일로 저장 → 확장 프로그램에서 읽기', async () => {
  const { impl } = wiki();
  const logs = [];
  const { meta, topics, vectorBytes } = await buildTopicData({ fetchImpl: impl, embedTexts: fakeEmbed, dim: FAKE_DIM, model: 'fake', log: (m) => logs.push(m) });
  assert.equal(meta.count, 200);
  assert.equal(topics[0].description, '음악 과학 0');
  assert.ok(logs.some((l) => l.includes('한국어 문서가 있는 주제 200개')));

  // 확장 프로그램이 fetch 로 읽는 것처럼 흉내 낸다
  const files = { 'data/topics.json': JSON.stringify({ meta, topics }), 'data/topic-vectors.bin': vectorBytes };
  const fetchImpl = async (url) => ({
    ok: url in files,
    json: async () => JSON.parse(files[url]),
    arrayBuffer: async () => files[url].buffer.slice(files[url].byteOffset, files[url].byteOffset + files[url].byteLength),
  });
  const loaded = await loadBundledTopics((p) => p, fetchImpl);
  assert.equal(loaded.topics.length, 200);
  assert.equal(loaded.vectors.length, 200 * FAKE_DIM);
  const [expected] = await fakeEmbed([`${topics[5].title}: ${topics[5].description}`]);
  assert.ok(dot(expected, loaded.vectors, 5 * FAKE_DIM) > 0.999);

  const report = await buildReport({ topics, vectors: loaded.vectors, dim: FAKE_DIM, embedTexts: fakeEmbed, meta });
  assert.match(report, /## 음악/);
  assert.match(report, /\| 상위 3% \|/);
});

test('내장 파일이 없으면 무엇을 해야 하는지 알려준다', async () => {
  await assert.rejects(loadBundledTopics((p) => p, async () => ({ ok: false })), /update\.bat/);
});

test('중심화: 모든 주제가 공유하는 방향을 빼서, 뜻이 다른 주제끼리의 유사도를 낮춘다', async () => {
  const { prepareIndex, toTopicSpace } = await import('../extension/src/lib/topic-space.js');
  const D = 4;
  // 모두 [1,0,0,0] 쪽으로 쏠린 벡터들 (e5 처럼 유사도가 한쪽에 몰린 상황)
  const raw = [
    [1, 0.2, 0, 0],
    [1, 0, 0.2, 0],
    [1, 0, 0, 0.2],
  ].map((v) => Float32Array.from(v.map((x) => x / Math.hypot(...v))));
  const flat = new Float32Array(3 * D);
  raw.forEach((v, i) => flat.set(v, i * D));
  const before = dot(raw[0], raw[1]);
  const index = prepareIndex({ meta: { dim: D }, topics: [{}, {}, {}], vectors: flat });
  const after = dot(index.vectors.subarray(0, D), index.vectors, D);
  assert.ok(before > 0.9, `before ${before}`);
  assert.ok(after < 0, `after ${after}`);
  // 영상 벡터도 같은 평균을 빼서 같은 공간으로 옮긴다
  const video = toTopicSpace(raw[0], index);
  assert.ok(dot(video, index.vectors, 0) > 0.99);
  assert.ok(Math.abs(Math.hypot(...video) - 1) < 1e-6);
});
