import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildInterestMap } from '../extension/src/lib/interest-map.js';
import { recommend, scoreTopics, applyFeedback, DEFAULT_PREFS, RANK_MIN, RANK_MAX } from '../extension/src/lib/recommender.js';
import { angleVec, videos } from './helpers.js';

const now = Date.parse('2026-10-11T12:00:00');
const dateKey = '2026-10-11';
const DIM = 8;

// 관심 묶음은 0° 방향. 주제 1000개를 0°~90° 에 고르게 깔면 각도 순서 = 가까운 순서가 된다.
function setup(n = 1000) {
  const topics = [];
  const vectors = new Float32Array(n * DIM);
  for (let i = 0; i < n; i++) {
    topics.push({ id: `T${i}`, title: `주제 ${i}` });
    vectors.set(angleVec((i / n) * (Math.PI / 2), DIM), i * DIM);
  }
  const vids = videos(['a', 'b', 'c', 'd', 'e', 'f'], { now }).map((v, i) => ({ ...v, vec: angleVec((i - 2.5) * 0.001, DIM), weight: 1 }));
  return { index: { topics, vectors, dim: DIM }, vids };
}

const rankOf = (rec) => Number(rec.topicId.slice(1)) / 1000;

test('관심사 바로 옆도, 아주 먼 것도 아닌 상위 몇 % 의 주제를 고른다', () => {
  const { index, vids } = setup();
  const rec = recommend({ videos: vids, index, dateKey });
  assert.ok(rec);
  const r = rankOf(rec);
  assert.ok(r > 0.005 && r < 0.04, `rank ${r}`);
  assert.ok(Math.abs(rec.rank - r) < 0.002, '보고된 순위가 실제 순위와 같아야 함');
});

test('목표 순위를 바꾸면 그만큼 멀거나 가까운 주제를 고른다', () => {
  const { index, vids } = setup();
  const near = rankOf(recommend({ videos: vids, index, dateKey, prefs: { targetRank: 0.01 } }));
  const far = rankOf(recommend({ videos: vids, index, dateKey, prefs: { targetRank: 0.08 } }));
  assert.ok(near < 0.02, `near ${near}`);
  assert.ok(far > 0.04, `far ${far}`);
});

test('최근 추천한 주제와 싫어요한 주제는 다시 고르지 않는다', () => {
  const { index, vids } = setup();
  const first = recommend({ videos: vids, index, dateKey });
  const second = recommend({ videos: vids, index, dateKey, recLog: [{ topicId: first.topicId, date: dateKey, reaction: 'like' }] });
  assert.notEqual(second.topicId, first.topicId);

  const muchLater = recommend({
    videos: vids,
    index,
    dateKey: '2027-12-01',
    recLog: [{ topicId: first.topicId, date: dateKey, reaction: 'dislike' }],
  });
  assert.notEqual(muchLater.topicId, first.topicId);
});

test('싫어요한 주제와 거의 같은 주제는 점수가 깎인다', () => {
  const { index, vids } = setup();
  const before = scoreTopics({ videos: vids, index, dateKey });
  const disliked = before[0].topic.id;
  const neighbor = before.find((c) => Math.abs(c.index - before[0].index) === 1);
  const after = scoreTopics({ videos: vids, index, dateKey, recLog: [{ topicId: disliked, date: dateKey, reaction: 'dislike' }] });
  const neighborAfter = after.find((c) => c.topic.id === neighbor.topic.id);
  assert.ok(neighborAfter.score < neighbor.score * 0.5);
});

test('재밌어요는 더 멀리, 별로예요는 더 가까이 (범위 제한)', () => {
  const liked = applyFeedback(DEFAULT_PREFS, 'like');
  assert.ok(liked.targetRank > DEFAULT_PREFS.targetRank);
  const disliked = applyFeedback(DEFAULT_PREFS, 'dislike');
  assert.ok(disliked.targetRank < DEFAULT_PREFS.targetRank);

  let p = DEFAULT_PREFS;
  for (let i = 0; i < 50; i++) p = applyFeedback(p, 'like');
  assert.equal(p.targetRank, RANK_MAX);
  for (let i = 0; i < 50; i++) p = applyFeedback(p, 'dislike');
  assert.equal(p.targetRank, RANK_MIN);
  assert.throws(() => applyFeedback(p, 'meh'));
});

test('서로 다른 두 관심사 "사이"의 막연한 주제는 가깝다고 치지 않는다', () => {
  // 0° 방향 영상 6개, 90° 방향 영상 6개. 둘을 평균하면 45° 가 되지만, 45° 주제는 어느 영상과도 가깝지 않다.
  const n = 91;
  const topics = Array.from({ length: n }, (_, i) => ({ id: `A${i}`, title: `${i}도` }));
  const vectors = new Float32Array(n * DIM);
  for (let i = 0; i < n; i++) vectors.set(angleVec((i * Math.PI) / 180, DIM), i * DIM);
  const vids = videos(Array.from({ length: 12 }, (_, i) => `v${i}`), { now }).map((v, i) => ({
    ...v,
    vec: angleVec(i < 6 ? 0 : Math.PI / 2, DIM),
    weight: 1,
  }));
  const ranked = scoreTopics({ videos: vids, index: { topics, vectors, dim: DIM }, prefs: { targetRank: 0.08 }, dateKey });
  const rankOfDeg = (d) => ranked.find((c) => c.topic.id === `A${d}`)?.rank ?? 1;
  assert.ok(rankOfDeg(45) > rankOfDeg(3), '45° 는 3° 보다 멀어야 함');
  assert.ok(rankOfDeg(45) > rankOfDeg(87), '45° 는 87° 보다 멀어야 함');
});

test('추천에는 가장 가까웠던 내 영상이 함께 기록된다', () => {
  const { index, vids } = setup();
  const rec = recommend({ videos: vids, index, dateKey });
  assert.equal(rec.nearVideos.length, 3);
  assert.ok(rec.nearVideos.every((v) => ['a', 'b', 'c', 'd', 'e', 'f'].includes(v.title)));
  assert.ok(rec.nearVideos[0].similarity >= rec.nearVideos[2].similarity);
});

test('관심 지도는 방향이 다른 영상들을 다른 묶음으로 나눈다', () => {
  const vids = videos(Array.from({ length: 12 }, (_, i) => `v${i}`), { now });
  const vecs = new Map(vids.map((v, i) => [v.id, angleVec(i < 6 ? 0 : Math.PI / 2, DIM)]));
  const map = buildInterestMap(vids, vecs, now);
  assert.equal(map.clusters.length, 2);
  assert.ok(Math.abs(map.clusters.reduce((s, c) => s + c.weight, 0) - 1) < 1e-6);
  assert.equal(map.clusters[0].examples.length, 3);
  assert.ok(map.clusters.every((c) => c.cohesion > 0.99), '같은 방향끼리 묶이면 비슷한 정도가 높다');
});

test('벡터가 없는 영상은 관심 지도에서 빠진다', () => {
  const vids = videos(['a', 'b'], { now });
  const map = buildInterestMap(vids, new Map([[vids[0].id, angleVec(0, DIM)]]), now);
  assert.equal(map.videoCount, 1);
});
