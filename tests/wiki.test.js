import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fetchVitalTitles, mapToKorean, addKoreanDescriptions, fetchSummary, topicText } from '../extension/src/lib/wiki.js';
import { fakeWikiFetch } from './helpers.js';

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

test('429·5xx 는 기다렸다 다시 시도하고, 계속 실패하면 상태 코드와 함께 알린다', async () => {
  const { getJson } = (await import('../extension/src/lib/wiki.js'))._test;
  const waits = [];
  const wait = async (ms) => void waits.push(ms);
  let n = 0;
  const flaky = async () => {
    n++;
    if (n === 1) return { ok: false, status: 429, headers: new Map([['retry-after', '3']]) };
    if (n === 2) return { ok: false, status: 503, headers: new Map() };
    return { ok: true, status: 200, json: async () => ({ query: { pages: [] } }) };
  };
  assert.deepEqual(await getJson(flaky, 'https://x/api', { wait }), { query: { pages: [] } });
  assert.deepEqual(waits, [3000, 4000]);

  const always = async () => ({ ok: false, status: 429, headers: new Map() });
  await assert.rejects(getJson(always, 'https://x/api', { wait }), /요청 실패 \(429\)/);

  const notFound = async () => ({ ok: false, status: 404, headers: new Map() });
  await assert.rejects(getJson(notFound, 'https://x/api', { wait: () => assert.fail('no retry') }), /\(404\)/);
});
