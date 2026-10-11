import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createEngine, videoText } from '../extension/src/lib/engine.js';
import { fetchVitalTitles, mapToKorean, addKoreanDescriptions, fetchSummary, topicText } from '../extension/src/lib/wiki.js';
import { memoryKv, fakeEmbed, fakeWikiFetch, FAKE_DIM } from './helpers.js';

const ROOT = 'Wikipedia:Vital articles/Level/4';

// 상위 페이지 → 섹션(사람 섹션은 빼야 함, 한 섹션은 넘겨주기) → 하위 페이지 구조
function wikiFixture(n = 150) {
  const arts = Array.from({ length: n }, (_, i) => ({ ns: 0, title: `Art ${i}` }));
  const vital = {
    [ROOT]: [
      { ns: 4, title: `${ROOT}/Arts` },
      { ns: 4, title: `${ROOT}/Science` },
      { ns: 4, title: `${ROOT}/People` },
      { ns: 4, title: 'Wikipedia:Vital articles/Level/5' },
    ],
    [`${ROOT}/Arts`]: [...arts.slice(0, 100), { ns: 4, title: `${ROOT}/Arts/More` }, { ns: 4, title: 'Wikipedia:Unrelated' }],
    [`${ROOT}/Arts/More`]: arts.slice(100),
    [`${ROOT}/Sciences (moved)`]: [{ ns: 0, title: 'Physics' }],
    [`${ROOT}/People`]: [{ ns: 0, title: 'Some Person' }],
  };
  const redirects = { [`${ROOT}/Science`]: `${ROOT}/Sciences (moved)` };
  const langlinks = {};
  const descriptions = {};
  arts.forEach(({ title }, i) => {
    if (i % 10 === 9) return; // 한국어판이 없는 문서
    langlinks[title] = `미술 ${i}`;
    descriptions[`미술 ${i}`] = `미술 분야 문서 ${i}`;
  });
  return { vital, redirects, langlinks, descriptions };
}

test('목록 페이지: 섹션을 찾아 넘겨주기·하위 페이지까지 따라가고, 사람 섹션과 다른 문서는 뺀다', async () => {
  const { impl } = fakeWikiFetch(wikiFixture());
  const { titles, diagnostics } = await fetchVitalTitles(impl);
  assert.ok(titles.includes('Art 149'));
  assert.ok(titles.includes('Physics'), '넘겨주기 따라감');
  assert.ok(!titles.includes('Some Person'), '사람 섹션 제외');
  assert.equal(titles.length, 151);
  assert.match(diagnostics, /목록 페이지/);
});

test('목록 페이지가 비어 있으면 분류에서 모은다', async () => {
  const talk = (prefix, n) => Array.from({ length: n }, (_, i) => ({ ns: 1, title: `Talk:${prefix} ${i}` }));
  const cat = 'Category:Wikipedia level-4 vital articles';
  const { impl } = fakeWikiFetch({
    vital: { [ROOT]: [] },
    categories: {
      [cat]: [
        { ns: 14, title: `${cat} in Arts` },
        { ns: 14, title: `${cat} in People` },
      ],
      [`${cat} in Arts`]: talk('Art', 1200),
      [`${cat} in People`]: talk('Person', 10),
    },
  });
  const { titles, diagnostics } = await fetchVitalTitles(impl);
  assert.equal(titles.length, 1200);
  assert.ok(titles.includes('Art 0'), '"Talk:" 제거');
  assert.match(diagnostics, /분류 2개까지 합쳐 1200개/);
});

test('한국어판이 없는 문서는 버리고, 설명을 붙인다', async () => {
  const { impl, calls } = fakeWikiFetch(wikiFixture());
  const ko = await mapToKorean(impl, Array.from({ length: 150 }, (_, i) => `Art ${i}`));
  assert.equal(ko.length, 135);
  assert.deepEqual(ko[0], { id: 'Art 0', title: '미술 0' });
  assert.ok(calls.every((u) => new URL(u).searchParams.get('titles').split('|').length <= 50), '한 번에 50개까지');
  await addKoreanDescriptions(impl, ko);
  assert.equal(ko[0].description, '미술 분야 문서 0');
  assert.equal(topicText(ko[0]), '미술 0: 미술 분야 문서 0');
  assert.ok(calls.every((u) => new URL(u).searchParams.get('origin') === '*'));
});

test('요약을 못 받아도 위키백과 링크는 준다', async () => {
  const { impl } = fakeWikiFetch({ vital: {}, langlinks: {}, descriptions: {}, summaries: { '재즈': { extract: '재즈는…', content_urls: { desktop: { page: 'https://ko.wikipedia.org/wiki/재즈' } } } } });
  assert.equal((await fetchSummary(impl, '재즈')).extract, '재즈는…');
  const missing = await fetchSummary(impl, '없는 문서');
  assert.equal(missing.extract, '');
  assert.equal(missing.url, 'https://ko.wikipedia.org/wiki/%EC%97%86%EB%8A%94_%EB%AC%B8%EC%84%9C');
});

test('주제 목록을 만들고, 다시 부르면 저장된 것을 쓴다', async () => {
  const kv = memoryKv();
  const { impl, calls } = fakeWikiFetch(wikiFixture());
  const statuses = [];
  const engine = createEngine({ kv, fetchImpl: impl, embedTexts: fakeEmbed, dim: FAKE_DIM, onStatus: (s) => statuses.push(s.phase) });
  // 섹션이 Arts 하나뿐인 픽스처라 다른 섹션 페이지는 링크 0개로 응답한다
  const index = await engine.ensureIndex();
  assert.equal(index.topics.length, 135);
  assert.equal(index.vectors.length, 135 * FAKE_DIM);
  assert.equal(statuses.at(-1), 'ready');

  const before = calls.length;
  const engine2 = createEngine({ kv, fetchImpl: impl, embedTexts: fakeEmbed, dim: FAKE_DIM });
  assert.equal((await engine2.ensureIndex()).topics.length, 135);
  assert.equal(calls.length, before, '네트워크를 다시 쓰지 않음');
});

test('임베딩 도중 멈춰도 다음에 이어서 한다', async () => {
  const kv = memoryKv();
  const { impl } = fakeWikiFetch(wikiFixture(400));
  let calls = 0;
  const flaky = async (texts) => {
    if (++calls === 10) throw new Error('service worker stopped');
    return fakeEmbed(texts);
  };
  const engine = createEngine({ kv, fetchImpl: impl, embedTexts: flaky, dim: FAKE_DIM });
  await assert.rejects(engine.ensureIndex(), /stopped/);
  const partial = await kv.get('topicVectorsPartial');
  assert.ok(partial.count >= 256, `저장된 진행분 ${partial?.count}`);

  let resumedTexts = 0;
  const counting = async (texts) => {
    resumedTexts += texts.length;
    return fakeEmbed(texts);
  };
  const engine2 = createEngine({ kv, fetchImpl: impl, embedTexts: counting, dim: FAKE_DIM });
  const index = await engine2.ensureIndex();
  assert.equal(index.topics.length, 360);
  assert.equal(resumedTexts, 360 - partial.count, '남은 것만 계산');
  // 이어서 만든 벡터도 처음부터 만든 것과 같아야 한다
  const [expected] = await fakeEmbed([topicText(index.topics[0])]);
  assert.deepEqual([...index.vectors.subarray(0, FAKE_DIM)], [...expected]);
});

test('목록이 너무 적으면 어디서 몇 개 나왔는지와 함께 오류로 알린다', async () => {
  const { impl } = fakeWikiFetch({});
  const statuses = [];
  const engine = createEngine({ kv: memoryKv(), fetchImpl: impl, embedTexts: fakeEmbed, dim: FAKE_DIM, onStatus: (s) => statuses.push(s) });
  await assert.rejects(engine.ensureIndex(), /너무 적어요 \(목록 페이지 \d+개에서 0개, 분류 1개까지 합쳐 0개\)/);
  assert.equal(statuses.at(-1).phase, 'error');
});

test('영상 벡터는 캐시하고, 제목·태그가 바뀌면 다시 계산한다', async () => {
  const kv = memoryKv();
  let embedded = 0;
  const engine = createEngine({
    kv,
    fetchImpl: () => assert.fail(),
    embedTexts: async (t) => {
      embedded += t.length;
      return fakeEmbed(t);
    },
    dim: FAKE_DIM,
  });
  const v = [{ id: 'a', title: '재즈 피아노' }, { id: 'b', title: '' }];
  assert.equal((await engine.embedVideos(v)).size, 1, '제목 없는 영상은 건너뜀');
  await engine.embedVideos(v);
  assert.equal(embedded, 1);
  await engine.embedVideos([{ id: 'a', title: '재즈 피아노', tags: ['jazz'] }]);
  assert.equal(embedded, 2);
  assert.equal(videoText({ title: 't', tags: ['a', 'b'] }), 't / a, b');
});
