// "살짝 빗나간" 주제 선택 + 반응 기반 거리 조절.

import { TOPICS } from '../data/topics.js';
import { DOMAINS } from '../data/domains.js';
import { DOMAIN_INDEX, embedTopic, distance } from './embedding.js';
import { withObjectParticle } from './korean.js';

export const DEFAULT_PREFS = Object.freeze({
  targetDistance: 0.45,
  spread: 0.12,
  domainBias: {},
});

export const TARGET_MIN = 0.2;
export const TARGET_MAX = 0.85;
const MIN_CLUSTER_WEIGHT = 0.08;
const REPEAT_COOLDOWN_DAYS = 60;
const BRIDGE_MIN_STRENGTH = 0.15;

const DOMAIN_LABELS = Object.fromEntries(DOMAINS.map((d) => [d.id, d.label]));
const TOPIC_VECTORS = new Map();

function topicVector(topic) {
  if (!TOPIC_VECTORS.has(topic.id)) TOPIC_VECTORS.set(topic.id, embedTopic(topic));
  return TOPIC_VECTORS.get(topic.id);
}

/**
 * 후보 주제마다 관심 지도와의 거리, 연결 군집, 점수를 계산한다.
 * 거리는 "가장 가까운 관심 군집"까지의 거리 — 그 군집이 추천의 연결고리가 된다.
 */
export function scoreTopics({ interestMap, prefs = DEFAULT_PREFS, recLog = [], dateKey = '', topics = TOPICS }) {
  const clusters = interestMap.clusters.filter((c) => c.weight >= MIN_CLUSTER_WEIGHT);
  if (clusters.length === 0) return [];

  const excluded = excludedTopicIds(recLog, dateKey);
  const target = prefs.targetDistance ?? DEFAULT_PREFS.targetDistance;
  const spread = prefs.spread ?? DEFAULT_PREFS.spread;

  return topics
    .filter((t) => !excluded.has(t.id))
    .map((topic) => {
      const vec = topicVector(topic);
      let anchor = clusters[0];
      let d = Infinity;
      for (const c of clusters) {
        const dc = distance(vec, c.centroid);
        if (dc < d) {
          d = dc;
          anchor = c;
        }
      }
      const fit = Math.exp(-((d - target) ** 2) / (2 * spread * spread));
      const score =
        fit *
        (0.4 + 0.6 * Math.sqrt(anchor.weight)) *
        biasFactor(topic, prefs.domainBias) *
        jitter(`${dateKey}:${topic.id}`);
      return { topic, distance: d, anchor, score };
    })
    .sort((a, b) => b.score - a.score);
}

/** 오늘의 추천 1개. 후보가 없으면 null */
export function recommend(args) {
  let ranked = scoreTopics(args);
  if (ranked.length === 0 && args.recLog?.length) {
    // 모든 주제를 최근에 봤다면, 싫어요를 받지 않은 주제에 한해 쿨다운을 풀어준다
    const disliked = new Set(args.recLog.filter((r) => r.reaction === 'dislike').map((r) => r.topicId));
    ranked = scoreTopics({ ...args, recLog: [], topics: (args.topics ?? TOPICS).filter((t) => !disliked.has(t.id)) });
  }
  const best = ranked[0];
  return best ? explain(best) : null;
}

/** 선택된 주제에 "○○을 좋아하시니까" 형태의 연결 이유를 붙인다 */
export function explain({ topic, distance: d, anchor }) {
  const bridgeDomain = pickBridgeDomain(topic, anchor.centroid);
  const anchorDomain = bridgeDomain ?? anchor.topDomains[0]?.[0];
  const label = DOMAIN_LABELS[anchorDomain] ?? '이런 주제';
  const bridgeText = (bridgeDomain && topic.bridges[bridgeDomain]) || '조금 다른 각도에서 보면 이어지는 지점이 있을 거예요.';
  return {
    topicId: topic.id,
    title: topic.title,
    summary: topic.summary,
    reason: `${withObjectParticle(label)} 좋아하시니까 이것도 재밌을 거예요. ${bridgeText}`,
    bridgeDomain: anchorDomain,
    anchorTitle: anchor.representative?.title ?? null,
    distance: Math.round(d * 1000) / 1000,
    link: `https://ko.wikipedia.org/w/index.php?search=${encodeURIComponent(topic.wiki ?? topic.title)}`,
  };
}

function pickBridgeDomain(topic, centroid) {
  let best = null;
  let bestStrength = BRIDGE_MIN_STRENGTH;
  for (const id of Object.keys(topic.bridges ?? {})) {
    const strength = centroid[DOMAIN_INDEX[id]] ?? 0;
    if (strength > bestStrength) {
      best = id;
      bestStrength = strength;
    }
  }
  return best;
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

function biasFactor(topic, domainBias = {}) {
  let s = 0;
  let total = 0;
  for (const [id, w] of Object.entries(topic.domains)) {
    s += w * (domainBias[id] ?? 0);
    total += w;
  }
  return Math.exp(total ? s / total : 0);
}

// 날짜+주제로 결정되는 ±5% 흔들림: 점수가 비슷한 후보 사이에서 매일 다른 선택이 나오게 한다.
function jitter(key) {
  let h = 2166136261;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return 0.95 + ((h >>> 0) / 0xffffffff) * 0.1;
}

/**
 * 반응에 따라 선호를 갱신한다 (불변 업데이트).
 * - 재밌어요: 목표 거리를 그 추천 쪽으로 끌어당기고 살짝 더 멀리 탐험한다.
 * - 별로예요: 목표 거리를 관심사 쪽으로 당긴다.
 * 두 경우 모두 해당 주제가 속한 분야에 대한 선호(bias)도 조금 조정한다.
 */
export function applyFeedback(prefs, rec, reaction, topics = TOPICS) {
  const target = prefs.targetDistance ?? DEFAULT_PREFS.targetDistance;
  let nextTarget = target;
  if (reaction === 'like') nextTarget = target + 0.25 * (rec.distance - target) + 0.03;
  else if (reaction === 'dislike') nextTarget = target - 0.05;
  else throw new Error(`unknown reaction: ${reaction}`);

  const domainBias = { ...(prefs.domainBias ?? {}) };
  const topic = topics.find((t) => t.id === rec.topicId);
  if (topic) {
    const sign = reaction === 'like' ? 1 : -1;
    for (const [id, w] of Object.entries(topic.domains)) {
      domainBias[id] = clamp((domainBias[id] ?? 0) + sign * 0.15 * w, -1, 1);
    }
  }
  return { ...prefs, targetDistance: clamp(nextTarget, TARGET_MIN, TARGET_MAX), domainBias };
}

function clamp(x, lo, hi) {
  return Math.min(hi, Math.max(lo, x));
}
