import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildInterestMap } from '../extension/src/lib/interest-map.js';
import { recommend, scoreTopics, applyFeedback, DEFAULT_PREFS, TARGET_MIN, TARGET_MAX } from '../extension/src/lib/recommender.js';
import { withObjectParticle } from '../extension/src/lib/korean.js';
import { videos, MUSIC_TITLES, GAME_TITLES } from './helpers.js';

const now = Date.parse('2026-10-11T12:00:00');
const dateKey = '2026-10-11';

test('관심 지도는 성격이 다른 영상들을 서로 다른 군집으로 나눈다', () => {
  const map = buildInterestMap(
    [...videos(MUSIC_TITLES, { now, categoryId: '10', prefix: 'm' }), ...videos(GAME_TITLES, { now, categoryId: '20', prefix: 'g' })],
    now,
  );
  const leads = map.clusters.map((c) => c.topDomains[0][0]).sort();
  assert.deepEqual(leads, ['games', 'music']);
  assert.ok(Math.abs(map.clusters.reduce((s, c) => s + c.weight, 0) - 1) < 1e-9);
});

test('너무 가깝지도 멀지도 않은 주제를 고른다', () => {
  const map = buildInterestMap(videos(MUSIC_TITLES, { now, categoryId: '10' }), now);
  const rec = recommend({ interestMap: map, dateKey });
  assert.ok(rec, '추천이 있어야 함');
  assert.ok(rec.distance > 0.2 && rec.distance < 0.75, `distance ${rec.distance}`);
  // 같은 분야의 정중앙 주제(평균율, 거리 최소)는 고르지 않는다
  const nearest = scoreTopics({ interestMap: map, dateKey }).reduce((a, b) => (a.distance < b.distance ? a : b));
  assert.notEqual(rec.topicId, nearest.topic.id);
});

test('연결 이유에 좋아하는 분야와 대표 영상이 들어간다', () => {
  const map = buildInterestMap(videos(GAME_TITLES, { now, categoryId: '20' }), now);
  const rec = recommend({ interestMap: map, dateKey });
  assert.match(rec.reason, /^게임을 좋아하시니까 이것도 재밌을 거예요\./);
  assert.ok(GAME_TITLES.includes(rec.anchorTitle));
  assert.match(rec.link, /^https:\/\/ko\.wikipedia\.org\//);
});

test('최근 추천했거나 싫어요한 주제는 다시 고르지 않는다', () => {
  const map = buildInterestMap(videos(MUSIC_TITLES, { now, categoryId: '10' }), now);
  const first = recommend({ interestMap: map, dateKey });
  const second = recommend({ interestMap: map, dateKey, recLog: [{ topicId: first.topicId, date: dateKey, reaction: 'like' }] });
  assert.notEqual(second.topicId, first.topicId);

  const later = recommend({
    interestMap: map,
    dateKey: '2027-06-01',
    recLog: [{ topicId: first.topicId, date: dateKey, reaction: 'dislike' }],
  });
  assert.notEqual(later.topicId, first.topicId);
});

test('모든 주제를 봤으면 싫어요 받지 않은 주제의 쿨다운을 푼다', () => {
  const map = buildInterestMap(videos(MUSIC_TITLES, { now, categoryId: '10' }), now);
  const all = scoreTopics({ interestMap: map, dateKey });
  const recLog = all.map((r, i) => ({ topicId: r.topic.id, date: dateKey, reaction: i === 0 ? 'dislike' : 'like' }));
  const rec = recommend({ interestMap: map, dateKey, recLog });
  assert.ok(rec);
  assert.notEqual(rec.topicId, all[0].topic.id);
});

test('재밌어요는 거리를 늘리고 별로예요는 줄인다 (범위 제한)', () => {
  const rec = { topicId: 'chiptune', distance: 0.45 };
  const liked = applyFeedback(DEFAULT_PREFS, rec, 'like');
  assert.ok(liked.targetDistance > DEFAULT_PREFS.targetDistance);
  assert.ok(liked.domainBias.music > 0);

  const disliked = applyFeedback(DEFAULT_PREFS, rec, 'dislike');
  assert.ok(disliked.targetDistance < DEFAULT_PREFS.targetDistance);
  assert.ok(disliked.domainBias.music < 0);

  let p = DEFAULT_PREFS;
  for (let i = 0; i < 50; i++) p = applyFeedback(p, { topicId: 'x', distance: 1 }, 'like');
  assert.equal(p.targetDistance, TARGET_MAX);
  for (let i = 0; i < 50; i++) p = applyFeedback(p, { topicId: 'x', distance: 0 }, 'dislike');
  assert.equal(p.targetDistance, TARGET_MIN);
  assert.equal(DEFAULT_PREFS.targetDistance, 0.45, '기본값은 변경되지 않아야 함');
});

test('목표 거리가 커지면 더 먼 주제를 추천한다', () => {
  const map = buildInterestMap(videos(MUSIC_TITLES, { now, categoryId: '10' }), now);
  const near = recommend({ interestMap: map, dateKey, prefs: { ...DEFAULT_PREFS, targetDistance: 0.25 } });
  const far = recommend({ interestMap: map, dateKey, prefs: { ...DEFAULT_PREFS, targetDistance: 0.75 } });
  assert.ok(far.distance > near.distance);
});

test('한국어 목적격 조사', () => {
  assert.equal(withObjectParticle('음악'), '음악을');
  assert.equal(withObjectParticle('심리'), '심리를');
  assert.equal(withObjectParticle('영화·애니메이션'), '영화·애니메이션을');
  assert.equal(withObjectParticle('IT'), 'IT을');
});
