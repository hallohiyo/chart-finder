// 사용자 관심 지도: 시청한 영상 벡터를 최근성 가중치와 함께 군집화한다.

import { dot, normalize } from './vector.js';

const DAY = 24 * 60 * 60 * 1000;
const HALF_LIFE_DAYS = 30;
const MAX_CLUSTERS = 8;
const VIDEOS_PER_CLUSTER = 4;
const KMEANS_ITERATIONS = 20;

/** 영상 1개의 가중치: 최근일수록, 여러 번 볼수록 크다 (반복은 로그로 완화) */
export function videoWeight(video, now = Date.now()) {
  const ageDays = Math.max(0, (now - (video.lastSeen ?? now)) / DAY);
  const recency = Math.pow(0.5, ageDays / HALF_LIFE_DAYS);
  return recency * (1 + Math.log(Math.max(1, video.count ?? 1)));
}

/**
 * @param {Array} videos 저장된 영상 레코드
 * @param {Map<string, Float32Array>} vectors 영상 ID → 정규화된 벡터
 */
export function buildInterestMap(videos, vectors, now = Date.now()) {
  const points = [];
  for (const video of videos) {
    const vec = vectors.get(video.id);
    if (vec) points.push({ video, vec, weight: videoWeight(video, now) });
  }
  if (points.length === 0) return { clusters: [], videoCount: 0 };

  const k = Math.min(MAX_CLUSTERS, Math.max(1, Math.ceil(points.length / VIDEOS_PER_CLUSTER)));
  const assignments = kmeans(points, k);
  const totalWeight = points.reduce((s, p) => s + p.weight, 0);

  const clusters = [];
  for (let c = 0; c < k; c++) {
    const members = points.filter((_, i) => assignments[i] === c);
    if (members.length === 0) continue;
    const centroid = weightedCentroid(members);
    const examples = members
      .map((p) => ({ title: p.video.title, score: dot(p.vec, centroid) * (0.5 + p.weight) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, 3)
      .map((e) => e.title);
    clusters.push({
      centroid,
      weight: members.reduce((s, p) => s + p.weight, 0) / totalWeight,
      size: members.length,
      examples,
    });
  }
  clusters.sort((a, b) => b.weight - a.weight);
  return { clusters, videoCount: points.length };
}

function weightedCentroid(points) {
  const c = new Float32Array(points[0].vec.length);
  for (const { vec, weight } of points) {
    for (let i = 0; i < c.length; i++) c[i] += vec[i] * weight;
  }
  return normalize(c);
}

// 결정적 k-means (코사인): 가중치가 가장 큰 점에서 시작해 먼 점을 차례로 초기 중심으로 삼는다.
function kmeans(points, k) {
  const centers = [points.reduce((best, p) => (p.weight > best.weight ? p : best), points[0]).vec];
  while (centers.length < k) {
    let far = null;
    let farScore = -1;
    for (const p of points) {
      const nearest = Math.max(...centers.map((c) => dot(p.vec, c)));
      const score = (1 - nearest) * p.weight;
      if (score > farScore) {
        farScore = score;
        far = p;
      }
    }
    if (!far || farScore <= 1e-9) break;
    centers.push(far.vec);
  }

  let assignments = new Array(points.length).fill(-1);
  for (let iter = 0; iter < KMEANS_ITERATIONS; iter++) {
    const next = points.map((p) => {
      let best = 0;
      let bestSim = -Infinity;
      centers.forEach((c, ci) => {
        const sim = dot(p.vec, c);
        if (sim > bestSim) {
          bestSim = sim;
          best = ci;
        }
      });
      return best;
    });
    const changed = next.some((a, i) => a !== assignments[i]);
    assignments = next;
    if (!changed) break;
    for (let ci = 0; ci < centers.length; ci++) {
      const members = points.filter((_, i) => assignments[i] === ci);
      if (members.length) centers[ci] = weightedCentroid(members);
    }
  }
  return assignments;
}
