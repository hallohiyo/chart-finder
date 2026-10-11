// "살짝 빗나간" 주제 선택 + 반응 기반 거리 조절.
//
// 거리는 유사도 값이 아니라 "순위"로 잰다. 모든 후보 주제를 내 관심 묶음과 가까운 순으로 줄 세웠을 때
// 상위 몇 %에 있는지(targetRank)를 목표로 삼는다. 임베딩 모델마다 유사도 값의 범위가 제각각이라
// 순위 기준이 모델이 바뀌어도 그대로 통한다.
// 실제 데이터(주제 6,741개, 중심화한 e5 벡터)로 본 감각:
//   상위 0.5% 이내: 이미 보고 있는 분야 (음악 → 클래식 음악, 가창)
//   상위 1~3%:     관심사와 이어지지만 평소엔 안 보는 분야 (음악 → 펑크, 녹음과 재생, 리듬)  ← 기본 목표
//   상위 6% 밖:     연결고리가 거의 안 보이는 분야

import { dot } from './vector.js';

export const DEFAULT_PREFS = Object.freeze({ targetRank: 0.015 });
export const RANK_MIN = 0.003;
export const RANK_MAX = 0.08;
const RANK_STEP = 1.3; // 반응 한 번에 목표 순위를 30%씩 옮긴다
const SPREAD = 0.6; // 로그 스케일 표준편차
const REPEAT_COOLDOWN_DAYS = 90;
const DISLIKE_SIMILARITY = 0.6; // 싫어요한 주제와 이만큼 비슷한 주제는 점수를 깎는다 (중심화 공간: 과당~포도당 0.79, 공수도~유도 0.51)
const DISLIKE_PENALTY = 0.3;

const TOP_K = 5; // 주제마다 가장 가까운 내 영상 몇 개의 평균으로 잴지
const MAX_VIDEOS = 400; // 계산량 제한: 가중치(최근·반복 시청)가 큰 영상부터

/**
 * 주제마다 "내 영상 중 가장 가까운 5개"와의 평균 유사도를 잰다.
 *
 * 예전엔 관심 묶음의 평균(중심)과 비교했는데, 영상이 수백 개면 묶음 안이 제각각이라
 * 평균이 흐릿해지고 "자연수", "삼각형" 같은 일반적인 주제가 가깝게 나왔다.
 * 실제 영상 여러 개와 직접 가까워야 점수가 높으니, 막연한 주제는 끼어들기 어렵다.
 *
 * @param videos [{id, title, vec(주제 공간), weight}]
 * @param index {topics, vectors(Float32Array, 길이 N*dim), dim}
 * @returns 점수 순으로 정렬된 후보
 */
export function scoreTopics({ videos, index, prefs = DEFAULT_PREFS, recLog = [], dateKey = '' }) {
  const { topics, vectors, dim } = index;
  const pool = [...videos].sort((a, b) => b.weight - a.weight).slice(0, MAX_VIDEOS);
  if (pool.length === 0 || topics.length === 0) return [];

  // 최근·자주 본 영상이 조금 더 무게를 갖도록 (0.6 ~ 1.0)
  const maxW = Math.max(...pool.map((v) => v.weight)) || 1;
  const boost = pool.map((v) => 0.6 + 0.4 * (v.weight / maxW));
  const k = Math.min(TOP_K, pool.length);

  // 1) 주제마다 가장 가까운 영상 k개의 평균 유사도
  const sims = new Float32Array(topics.length);
  const top = new Float32Array(k);
  for (let t = 0; t < topics.length; t++) {
    top.fill(-Infinity);
    for (let v = 0; v < pool.length; v++) {
      const s = dot(pool[v].vec, vectors, t * dim) * boost[v];
      if (s > top[k - 1]) {
        let i = k - 1;
        while (i > 0 && top[i - 1] < s) {
          top[i] = top[i - 1];
          i--;
        }
        top[i] = s;
      }
    }
    let sum = 0;
    for (let i = 0; i < k; i++) sum += top[i];
    sims[t] = sum / k;
  }

  // 2) 유사도 순위 → 백분위 (0 = 가장 가까움)
  const order = [...sims.keys()].sort((a, b) => sims[b] - sims[a]);
  const rank = new Float32Array(topics.length);
  order.forEach((t, r) => {
    rank[t] = (r + 0.5) / topics.length;
  });

  const excluded = excludedTopicIds(recLog, dateKey);
  const dislikedIdx = recLog
    .filter((r) => r.reaction === 'dislike')
    .map((r) => topics.findIndex((t) => t.id === r.topicId))
    .filter((i) => i >= 0);

  const target = clamp(prefs.targetRank ?? DEFAULT_PREFS.targetRank, RANK_MIN, RANK_MAX);
  const out = [];
  for (let t = 0; t < topics.length; t++) {
    if (excluded.has(topics[t].id)) continue;
    const fit = Math.exp(-(Math.log(rank[t] / target) ** 2) / (2 * SPREAD * SPREAD));
    if (fit < 0.01) continue;
    let score = fit * jitter(`${dateKey}:${topics[t].id}`);
    if (dislikedIdx.some((d) => dot(vectors.subarray(d * dim, (d + 1) * dim), vectors, t * dim) > DISLIKE_SIMILARITY)) {
      score *= DISLIKE_PENALTY;
    }
    out.push({ index: t, topic: topics[t], rank: rank[t], similarity: sims[t], score });
  }
  return out.sort((a, b) => b.score - a.score);
}

/** 이 주제와 가장 가까웠던 내 영상 (설정 페이지에서 "왜 이게 나왔나" 확인용) */
export function nearestVideos(topicIndex, videos, index, n = 3) {
  return videos
    .map((v) => ({ title: v.title, similarity: dot(v.vec, index.vectors, topicIndex * index.dim) }))
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, n)
    .map((v) => ({ title: v.title, similarity: round(v.similarity) }));
}

/** 오늘의 추천 1개. 후보가 없으면 null */
export function recommend(args) {
  let ranked = scoreTopics(args);
  if (ranked.length === 0 && args.recLog?.length) {
    // 근처 주제를 다 봤으면 싫어요 받지 않은 주제에 한해 쿨다운을 풀어준다
    const keep = args.recLog.filter((r) => r.reaction === 'dislike');
    ranked = scoreTopics({ ...args, recLog: keep });
  }
  const best = ranked[0];
  if (!best) return null;
  return {
    topicId: best.topic.id,
    title: best.topic.title,
    description: best.topic.description ?? '',
    rank: round(best.rank),
    similarity: round(best.similarity),
    nearVideos: nearestVideos(best.index, args.videos, args.index),
  };
}

function excludedTopicIds(recLog, dateKey) {
  const out = new Set();
  const today = dateKey ? Date.parse(dateKey) : NaN;
  for (const r of recLog) {
    if (r.reaction === 'dislike') out.add(r.topicId);
    else if (Number.isNaN(today) || today - Date.parse(r.date) < REPEAT_COOLDOWN_DAYS * 86400000) out.add(r.topicId);
  }
  return out;
}

// 날짜+주제로 정해지는 ±10% 흔들림: 점수가 비슷한 후보 중 날마다 다른 게 뽑히게 한다.
function jitter(key) {
  let h = 2166136261;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return 0.9 + ((h >>> 0) / 0xffffffff) * 0.2;
}

/**
 * 반응에 따라 목표 순위를 옮긴다 (불변 업데이트).
 * 재밌어요 → 조금 더 먼 주제로, 별로예요 → 관심사에 조금 더 가까운 주제로.
 */
export function applyFeedback(prefs, reaction) {
  const target = prefs.targetRank ?? DEFAULT_PREFS.targetRank;
  let next;
  if (reaction === 'like') next = target * RANK_STEP;
  else if (reaction === 'dislike') next = target / RANK_STEP;
  else throw new Error(`unknown reaction: ${reaction}`);
  return { ...prefs, targetRank: clamp(next, RANK_MIN, RANK_MAX) };
}

function clamp(x, lo, hi) {
  return Math.min(hi, Math.max(lo, x));
}

function round(x) {
  return Math.round(x * 10000) / 10000;
}
