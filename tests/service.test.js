import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore, pruneVideos, upsertVideo, localDateKey } from '../extension/src/lib/store.js';
import { createService, MIN_VIDEOS } from '../extension/src/lib/service.js';
import { parseWatchUrl, cleanPageTitle, fetchVideoMetadata } from '../extension/src/lib/youtube.js';
import { fetchSummary } from '../extension/src/lib/wiki.js';
import { createEngine } from '../extension/src/lib/engine.js';
import { buildTopicData } from '../scripts/topic-data.mjs';
import { decodeVectors } from '../extension/src/lib/quantize.js';
import { memoryStorage, memoryKv, fakeEmbed, fakeWikiFetch, FAKE_DIM } from './helpers.js';

const ROOT = 'Wikipedia:Vital articles/Level/4';
const MUSIC_TITLES = ['재즈 피아노 연주', '기타 코드 진행', '밴드 라이브 공연', '힙합 앨범 감상', '클래식 오케스트라', '드럼 연주 영상'];

// 음악·과학·요리 단어가 섞인 가짜 주제 300개
function wiki() {
  const words = ['음악', '재즈', '피아노', '음향', '물리', '요리', '역사', '수학', '공연', '연주', '화학', '건축'];
  const links = [];
  const langlinks = {};
  const descriptions = {};
  for (let i = 0; i < 300; i++) {
    const en = `Topic ${i}`;
    const ko = `주제${i}`;
    links.push({ ns: 0, title: en });
    langlinks[en] = ko;
    descriptions[ko] = `${words[i % words.length]} ${words[(i * 7) % words.length]} ${words[(i * 5 + 3) % words.length]}`;
  }
  return fakeWikiFetch({
    vital: { [`${ROOT}/Arts`]: links },
    langlinks,
    descriptions,
    summaries: Object.fromEntries(Object.values(langlinks).map((t) => [t, { extract: `${t} 요약`, content_urls: { desktop: { page: `https://ko.wikipedia.org/wiki/${t}` } } }])),
  });
}

let bundledPromise = null;
function bundledData() {
  bundledPromise ??= buildTopicData({ fetchImpl: wiki().impl, embedTexts: fakeEmbed, dim: FAKE_DIM, model: 'fake' }).then(
    ({ meta, topics, vectorBytes }) => ({ meta, topics, vectors: decodeVectors(vectorBytes, meta.count, meta.dim) }),
  );
  return bundledPromise;
}

function setup(startAt = Date.parse('2026-10-11T09:00:00')) {
  const storage = memoryStorage();
  const store = createStore(storage);
  const clock = { t: startAt };
  const { impl } = wiki();
  const engine = createEngine({ kv: memoryKv(), loadBundled: bundledData, embedTexts: fakeEmbed, onStatus: (s) => store.setIndexStatus(s) });
  const service = createService(store, engine, {
    now: () => clock.t,
    fetchImpl: () => assert.fail('network not expected'),
    fetchSummary: (title) => fetchSummary(impl, title),
  });
  return { storage, store, service, clock, engine };
}

async function ready(service, engine, clock) {
  await watchAll(service, MUSIC_TITLES, clock);
  await engine.warmUp();
}

async function watchAll(service, titles, clock) {
  for (const [i, title] of titles.entries()) {
    await service.recordWatch({ videoId: `vid${String(i).padStart(8, '0')}`, title });
    clock.t += 60 * 1000;
  }
}

test('youtube.com/watch 주소만 영상 ID로 인정한다', () => {
  assert.equal(parseWatchUrl('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10'), 'dQw4w9WgXcQ');
  assert.equal(parseWatchUrl('https://m.youtube.com/watch?v=dQw4w9WgXcQ'), 'dQw4w9WgXcQ');
  assert.equal(parseWatchUrl('https://www.youtube.com/results?search_query=x'), null);
  assert.equal(parseWatchUrl('https://evil.com/watch?v=dQw4w9WgXcQ'), null);
  assert.equal(parseWatchUrl('https://www.youtube.com.evil.com/watch?v=dQw4w9WgXcQ'), null);
  assert.equal(parseWatchUrl('https://www.youtube.com/watch?v=short'), null);
  assert.equal(parseWatchUrl('not a url'), null);
  assert.equal(cleanPageTitle('(3) 재즈 피아노 - YouTube'), '재즈 피아노');
});

test('기록이 부족하면 수집 중 상태를 알려준다', async () => {
  const { service, clock } = setup();
  await watchAll(service, MUSIC_TITLES.slice(0, MIN_VIDEOS - 1), clock);
  const today = await service.getToday();
  assert.equal(today.status, 'collecting');
  assert.equal(today.recommendation, null);
  assert.equal(today.videoCount, MIN_VIDEOS - 1);
});

test('모델을 처음 받을 땐 준비 상태를 보여주고 내려받기를 시작한다', async () => {
  const { service, engine, clock } = setup();
  await watchAll(service, MUSIC_TITLES, clock);
  const first = await service.getToday();
  assert.equal(first.status, 'preparing');
  await engine.warmUp();
  const after = await service.getToday();
  assert.equal(after.status, 'ready');
  assert.equal(after.indexStatus.phase, 'ready');
});

test('예전에 받아 둔 모델이면 (서비스 워커가 다시 켜져도) 기다렸다가 바로 추천한다', async () => {
  const { service, store, clock } = setup();
  await watchAll(service, MUSIC_TITLES, clock);
  await store.setIndexStatus({ phase: 'ready' });
  assert.equal((await service.getToday()).status, 'ready');
});

test('추천에는 요약과 위키백과·유튜브 링크가 붙고, 연결 이유는 없다', async () => {
  const { service, engine, clock } = setup();
  await ready(service, engine, clock);
  const { recommendation: rec } = await service.getToday();
  assert.match(rec.title, /^주제\d+$/);
  assert.equal(rec.summary, `${rec.title} 요약`);
  assert.equal(rec.url, `https://ko.wikipedia.org/wiki/${rec.title}`);
  assert.equal(rec.youtubeUrl, `https://www.youtube.com/results?search_query=${encodeURIComponent(rec.title)}`);
  assert.equal(rec.reason, undefined);
});

test('하루 추천 한도를 지키고, 같은 날에는 같은 추천을 보여준다', async () => {
  const { service, engine, clock } = setup();
  await ready(service, engine, clock);

  const a = await service.getToday();
  assert.equal(a.status, 'ready');
  const b = await service.getToday();
  assert.equal(b.recommendation.topicId, a.recommendation.topicId);
  assert.equal(a.canGetMore, false);

  // 반응 전에는 다음 추천이 없다
  assert.equal((await service.next()).recommendation.topicId, a.recommendation.topicId);

  const reacted = await service.react(a.recommendation.topicId, 'like');
  assert.equal(reacted.recommendation.reaction, 'like');
  assert.equal(reacted.canGetMore, false, '기본 한도는 하루 1개');
  assert.equal((await service.next()).recommendation.topicId, a.recommendation.topicId);

  // 다음 날에는 새 추천
  clock.t += 24 * 60 * 60 * 1000;
  const tomorrow = await service.getToday();
  assert.equal(tomorrow.status, 'ready');
  assert.notEqual(tomorrow.recommendation.topicId, a.recommendation.topicId);
  assert.equal(tomorrow.recommendation.reaction, null);
});

test('팝업을 동시에 여러 번 열어도 추천은 하나만 만든다', async () => {
  const { service, engine, clock, store } = setup();
  await ready(service, engine, clock);
  const results = await Promise.all([service.getToday(), service.getToday(), service.getToday()]);
  assert.equal(new Set(results.map((r) => r.recommendation.topicId)).size, 1);
  assert.equal((await store.getDaily()).recs.length, 1);
  assert.equal((await store.getRecLog()).length, 1);
});

test('한도를 늘리면 반응 후 하나 더 받을 수 있다', async () => {
  const { service, store, engine, clock } = setup();
  await store.updateSettings({ maxPerDay: 2 });
  await ready(service, engine, clock);
  const first = await service.getToday();
  const afterReact = await service.react(first.recommendation.topicId, 'dislike');
  assert.equal(afterReact.canGetMore, true);
  const second = await service.next();
  assert.equal(second.index, 2);
  assert.notEqual(second.recommendation.topicId, first.recommendation.topicId);
  await service.react(second.recommendation.topicId, 'like');
  assert.equal((await service.next()).index, 2);
});

test('반응은 한 번만 선호도에 반영된다', async () => {
  const { service, store, engine, clock } = setup();
  await ready(service, engine, clock);
  const { recommendation } = await service.getToday();
  await service.react(recommendation.topicId, 'dislike');
  const once = (await store.getPrefs()).targetRank;
  await service.react(recommendation.topicId, 'dislike');
  assert.equal((await store.getPrefs()).targetRank, once);
  assert.ok(once < 0.03);
});

test('설정 페이지 개요: 주제 수와 관심 묶음 예시 영상', async () => {
  const { service, store, engine, clock } = setup();
  await store.updateSettings({ apiKey: 'AIzaSECRET1234' });
  await ready(service, engine, clock);
  const o = await service.overview();
  assert.equal(o.settings.apiKey, '••••1234');
  assert.equal(o.topicCount, 300);
  assert.equal(o.modelReady, true);
  assert.equal(o.recentVideos.length, MUSIC_TITLES.length);
  assert.equal(o.recentVideos[0].title, MUSIC_TITLES.at(-1), '최근 본 순서');
  assert.ok(o.clusters.length >= 1);
  assert.ok(o.clusters.every((c) => c.examples.every((t) => MUSIC_TITLES.includes(t))));
});

test('일시정지 중에는 기록하지 않고, 잘못된 ID도 무시한다', async () => {
  const { service, store } = setup();
  await store.updateSettings({ paused: true });
  assert.equal((await service.recordWatch({ videoId: 'dQw4w9WgXcQ', title: 'x' })).recorded, false);
  await store.updateSettings({ paused: false });
  assert.equal((await service.recordWatch({ videoId: '../../etc', title: 'x' })).recorded, false);
  assert.deepEqual(await store.getVideos(), {});
});

test('API 키가 있으면 메타데이터를 채운다', async () => {
  const storage = memoryStorage();
  const store = createStore(storage);
  let requested = '';
  const fetchImpl = async (url) => {
    requested = url;
    return {
      ok: true,
      json: async () => ({
        items: [{ id: 'dQw4w9WgXcQ', snippet: { title: '정식 제목', description: '설명', tags: ['재즈'], categoryId: '10' } }],
      }),
    };
  };
  const service = createService(store, null, { fetchImpl });
  await service.recordWatch({ videoId: 'dQw4w9WgXcQ', title: '페이지 제목' });
  await service.recordWatch({ videoId: 'aaaaaaaaaaa', title: '삭제된 영상' });

  assert.equal((await service.enrichPending()).skipped, 'no-api-key');
  await store.updateSettings({ apiKey: ' KEY ' });
  await service.enrichPending();

  assert.match(requested, /^https:\/\/www\.googleapis\.com\/youtube\/v3\/videos\?part=snippet&id=/);
  assert.match(requested, /key=KEY$/);
  const v = await store.getVideos();
  assert.equal(v.dQw4w9WgXcQ.title, '정식 제목');
  assert.deepEqual(v.dQw4w9WgXcQ.tags, ['재즈']);
  assert.equal(v.aaaaaaaaaaa.enriched, true, '결과에 없는 영상도 재조회하지 않음');
  assert.equal(v.aaaaaaaaaaa.title, '삭제된 영상');
  assert.deepEqual(await store.pendingEnrichIds(), []);
});

test('API 오류는 이유와 함께 던지고 대기열을 유지한다', async () => {
  const fetchImpl = async () => ({ ok: false, status: 403, json: async () => ({ error: { errors: [{ reason: 'quotaExceeded' }] } }) });
  await assert.rejects(fetchVideoMetadata(['dQw4w9WgXcQ'], 'k', fetchImpl), /403 \(quotaExceeded\)/);
});

test('재시청은 30분 간격으로만 센다, 오래된 기록은 지운다', () => {
  const t0 = Date.parse('2026-01-01T00:00:00');
  const videos = {};
  upsertVideo(videos, { videoId: 'dQw4w9WgXcQ', title: 'a' }, t0);
  upsertVideo(videos, { videoId: 'dQw4w9WgXcQ', title: 'a' }, t0 + 10 * 60 * 1000);
  assert.equal(videos.dQw4w9WgXcQ.count, 1);
  upsertVideo(videos, { videoId: 'dQw4w9WgXcQ', title: 'a' }, t0 + 60 * 60 * 1000);
  assert.equal(videos.dQw4w9WgXcQ.count, 2);

  assert.deepEqual(pruneVideos(videos, t0 + 200 * 86400000), {});
  assert.equal(localDateKey(new Date(2026, 0, 5)), '2026-01-05');
});

test('데이터 삭제는 설정을 남긴다', async () => {
  const { service, store, clock, engine } = setup();
  await store.updateSettings({ apiKey: 'KEY', maxPerDay: 9 });
  assert.equal((await store.getSettings()).maxPerDay, 3, '한도는 3개로 제한');
  await ready(service, engine, clock);
  await service.getToday();
  await store.clearData();
  assert.deepEqual(await store.getVideos(), {});
  assert.equal(await store.getDaily(), null);
  assert.equal((await store.getSettings()).apiKey, 'KEY');
});

test('동시 기록도 유실되지 않는다', async () => {
  const { service, store } = setup();
  await Promise.all(
    Array.from({ length: 20 }, (_, i) => service.recordWatch({ videoId: `con${String(i).padStart(8, '0')}`, title: '재즈' })),
  );
  assert.equal(Object.keys(await store.getVideos()).length, 20);
});
