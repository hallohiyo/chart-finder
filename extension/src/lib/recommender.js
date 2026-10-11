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
const MIN_CLUSTER_WEIGHT = 0.08;
const REPEAT_COOLDOWN_DAYS = 90;
const DISLIKE_SIMILARITY = 0.6; // 싫어요한 주제와 이만큼 비슷한 주제는 점수를 깎는다 (중심화 공간: 과당~포도당 0.79, 공수도~유도 0.51)
const DISLIKE_PENALTY = 0.3;

/**
 * @param interestMap buildInterestMap 결과
 * @param index {topics, vectors(Float32Array, 길이 N*dim), dim}
 * @returns 점수 순으로 정렬된 후보
 */
export function scoreTopics({ interestMap, index, prefs = DEFAULT_PREFS, recLog = [], dateKey = '' }) {
  const clusters = interestMap.clusters.filter((c) => c.weight >= MIN_CLUSTER_WEIGHT);
  const { topics, vectors, dim } = index;
  if (clusters.length === 0 || topics.length === 0) return [];

  // 1) 주제마다 가장 가까운 관심 묶음과의 유사도
  const sims = new Float32Array(topics.length);
  const anchors = new Int32Array(topics.length);
  for (let t = 0; t < topics.length; t++) {
    let best = -Infinity;
    clusters.forEach((c, ci) => {
      const s = dot(c.centroid, vectors, t * dim);
      if (s > best) {
        best = s;
        anchors[t] = ci;
      }
    });
    sims[t] = best;
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
    const anchor = clusters[anchors[t]];
    let score = fit * (0.4 + 0.6 * Math.sqrt(anchor.weight)) * jitter(`${dateKey}:${topics[t].id}`);
    if (dislikedIdx.some((d) => dot(vectors.subarray(d * dim, (d + 1) * dim), vectors, t * dim) > DISLIKE_SIMILARITY)) {
      score *= DISLIKE_PENALTY;
    }
    out.push({ index: t, topic: topics[t], rank: rank[t], similarity: sims[t], score });
  }
  return out.sort((a, b) => b.score - a.score);
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
