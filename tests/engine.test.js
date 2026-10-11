import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createEngine, videoText } from '../extension/src/lib/engine.js';
import { memoryKv, fakeEmbed, FAKE_DIM } from './helpers.js';

const bundled = async () => ({
  meta: { dim: FAKE_DIM, count: 1, builtAt: '2026-10-11T00:00:00Z' },
  topics: [{ id: 'Jazz', title: '재즈' }],
  vectors: (await fakeEmbed(['재즈']))[0],
});

test('모델 준비: 처음엔 내려받기 진행률을 알리고, 끝나면 준비 완료', async () => {
  const statuses = [];
  let calls = 0;
  const embedTexts = async (texts, onProgress) => {
    calls++;
    onProgress?.({ loaded: 60e6, total: 120e6 });
    return fakeEmbed(texts);
  };
  const engine = createEngine({ kv: memoryKv(), loadBundled: bundled, embedTexts, onStatus: (s) => statuses.push(s) });
  assert.equal(engine.isModelReady(), false);
  await Promise.all([engine.warmUp(), engine.warmUp()]);
  assert.equal(calls, 1, '동시에 불려도 한 번만');
  assert.equal(engine.isModelReady(), true);
  assert.deepEqual(statuses.map((s) => s.phase), ['downloading', 'downloading', 'ready']);
  assert.equal(statuses.at(-1).check, null, '점검용 문장이 없는 데이터');
  assert.equal(statuses[1].done, 60e6);
  await engine.warmUp();
  assert.equal(calls, 1, '준비된 뒤엔 다시 안 함');
});

test('자가 점검: 브라우저 모델이 내장 데이터와 같은 벡터를 내는지 잰다', async () => {
  const [probe] = await fakeEmbed(['재즈 피아노']);
  const withProbes = async () => ({ ...(await bundled()), meta: { dim: FAKE_DIM, count: 1, probes: [{ text: '재즈 피아노', vec: Array.from(probe) }] } });
  const run = async (embedTexts) => {
    const statuses = [];
    await createEngine({ kv: memoryKv(), loadBundled: withProbes, embedTexts, onStatus: (s) => statuses.push(s) }).warmUp();
    return statuses.at(-1).check;
  };
  assert.ok((await run(fakeEmbed)) > 0.999, '같은 모델이면 일치');
  const different = async (texts) => fakeEmbed(texts.map((t) => `${t} 완전히 다른 결과`));
  assert.ok((await run(different)) < 0.9, '다른 결과면 낮게 나옴');
});

test('모델 준비 실패는 오류로 알리고, 다음에 다시 시도할 수 있다', async () => {
  const statuses = [];
  let fail = true;
  const embedTexts = async (t) => {
    if (fail) throw new Error('network down');
    return fakeEmbed(t);
  };
  const engine = createEngine({ kv: memoryKv(), loadBundled: bundled, embedTexts, onStatus: (s) => statuses.push(s) });
  await assert.rejects(engine.warmUp(), /network down/);
  assert.equal(statuses.at(-1).phase, 'error');
  fail = false;
  await engine.warmUp();
  assert.equal(engine.isModelReady(), true);
});

test('내장 데이터는 한 번만 읽는다', async () => {
  let reads = 0;
  const engine = createEngine({ kv: memoryKv(), loadBundled: () => (reads++, bundled()), embedTexts: fakeEmbed });
  const [a, b] = await Promise.all([engine.loadIndex(), engine.loadIndex()]);
  assert.equal(a, b);
  assert.equal(reads, 1);
  assert.equal(a.dim, FAKE_DIM);
  assert.equal(a.topics[0].title, '재즈');
});

test('영상 벡터는 캐시하고, 제목·태그가 바뀌면 다시 계산한다', async () => {
  const kv = memoryKv();
  let embedded = 0;
  const engine = createEngine({
    kv,
    loadBundled: bundled,
    embedTexts: async (t) => {
      embedded += t.length;
      return fakeEmbed(t);
    },
  });
  const v = [{ id: 'a', title: '재즈 피아노' }, { id: 'b', title: '' }];
  assert.equal((await engine.embedVideos(v)).size, 1, '제목 없는 영상은 건너뜀');
  await engine.embedVideos(v);
  assert.equal(embedded, 1);
  await engine.embedVideos([{ id: 'a', title: '재즈 피아노', tags: ['jazz'] }]);
  assert.equal(embedded, 2);
  assert.equal(videoText({ title: 't', tags: ['a', 'b'] }), 't / a, b');
});
