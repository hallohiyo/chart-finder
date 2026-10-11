import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore, pruneVideos, upsertVideo, localDateKey } from '../extension/src/lib/store.js';
import { createService, MIN_VIDEOS } from '../extension/src/lib/service.js';
import { parseWatchUrl, cleanPageTitle, fetchVideoMetadata } from '../extension/src/lib/youtube.js';
import { memoryStorage, MUSIC_TITLES } from './helpers.js';

function setup(startAt = Date.parse('2026-10-11T09:00:00')) {
  const storage = memoryStorage();
  const store = createStore(storage);
  const clock = { t: startAt };
  const service = createService(store, { now: () => clock.t, fetchImpl: () => assert.fail('network not expected') });
  return { storage, store, service, clock };
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

test('하루 추천 한도를 지키고, 같은 날에는 같은 추천을 보여준다', async () => {
  const { service, clock } = setup();
  await watchAll(service, MUSIC_TITLES, clock);

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

test('한도를 늘리면 반응 후 하나 더 받을 수 있다', async () => {
  const { service, store, clock } = setup();
  await store.updateSettings({ maxPerDay: 2 });
  await watchAll(service, MUSIC_TITLES, clock);
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
  const { service, store, clock } = setup();
  await watchAll(service, MUSIC_TITLES, clock);
  const { recommendation } = await service.getToday();
  await service.react(recommendation.topicId, 'dislike');
  const once = (await store.getPrefs()).targetDistance;
  await service.react(recommendation.topicId, 'dislike');
  assert.equal((await store.getPrefs()).targetDistance, once);
  assert.ok(once < 0.45);
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
  const service = createService(store, { fetchImpl });
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
  const { service, store, clock } = setup();
  await store.updateSettings({ apiKey: 'KEY', maxPerDay: 9 });
  assert.equal((await store.getSettings()).maxPerDay, 3, '한도는 3개로 제한');
  await watchAll(service, MUSIC_TITLES, clock);
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

test('개요는 API 키를 가린다', async () => {
  const { service, store } = setup();
  await store.updateSettings({ apiKey: 'AIzaSECRET1234' });
  const o = await service.overview();
  assert.equal(o.settings.apiKey, '••••1234');
});
