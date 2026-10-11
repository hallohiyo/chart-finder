// 사용자 관심 지도: 시청한 영상 임베딩을 최근성 가중치와 함께 군집화한다.

import { DIM, embedVideo, cosine, distance, normalize, topDomains } from './embedding.js';

const DAY = 24 * 60 * 60 * 1000;
const HALF_LIFE_DAYS = 30;
const MAX_CLUSTERS = 4;
const VIDEOS_PER_CLUSTER = 5;
const KMEANS_ITERATIONS = 15;

/** 영상 1개의 가중치: 최근일수록, 여러 번 볼수록 크다 (반복은 로그로 완화) */
export function videoWeight(video, now = Date.now()) {
  const ageDays = Math.max(0, (now - (video.lastSeen ?? now)) / DAY);
  const recency = Math.pow(0.5, ageDays / HALF_LIFE_DAYS);
  return recency * (1 + Math.log(Math.max(1, video.count ?? 1)));
}

/**
 * @param {Array} videos 저장된 영상 레코드 배열
 * @returns {{clusters: Array, videoCount: number, profile: Float64Array|null}}
 */
export function buildInterestMap(videos, now = Date.now()) {
  const points = [];
  for (const video of videos) {
    const vec = embedVideo(video);
    if (!vec) continue;
    points.push({ video, vec, weight: videoWeight(video, now) });
  }
  if (points.length === 0) return { clusters: [], videoCount: 0, profile: null };

  const k = Math.min(MAX_CLUSTERS, Math.max(1, Math.ceil(points.length / VIDEOS_PER_CLUSTER)));
  const assignments = kmeans(points, k);

  const totalWeight = points.reduce((s, p) => s + p.weight, 0);
  const clusters = [];
  for (let c = 0; c < k; c++) {
    const members = points.filter((_, i) => assignments[i] === c);
    if (members.length === 0) continue;
    const centroid = weightedCentroid(members);
    const weight = members.reduce((s, p) => s + p.weight, 0) / totalWeight;
    // 대표 영상: 중심과 가깝고 가중치가 큰 영상
    const representative = members
      .map((p) => ({ p, score: cosine(p.vec, centroid) * (0.5 + p.weight) }))
      .sort((a, b) => b.score - a.score)[0].p.video;
    clusters.push({
      centroid,
      weight,
      size: members.length,
      topDomains: topDomains(centroid, 3),
      representative: { id: representative.id, title: representative.title },
    });
  }
  clusters.sort((a, b) => b.weight - a.weight);

  return { clusters, videoCount: points.length, profile: weightedCentroid(points) };
}

function weightedCentroid(points) {
  const c = new Float64Array(DIM);
  for (const { vec, weight } of points) {
    for (let i = 0; i < DIM; i++) c[i] += vec[i] * weight;
  }
  return normalize(c);
}

// 결정적 k-means: 가중치가 가장 큰 점에서 시작해 먼 점을 순서대로 초기 중심으로 삼는다.
function kmeans(points, k) {
  const centers = [];
  const first = points.reduce((best, p) => (p.weight > best.weight ? p : best), points[0]);
  centers.push(first.vec);
  while (centers.length < k) {
    let far = null;
    let farDist = -1;
    for (const p of points) {
      const d = Math.min(...centers.map((c) => distance(p.vec, c))) * p.weight;
      if (d > farDist) {
        farDist = d;
        far = p;
      }
    }
    if (!far || farDist <= 0) break;
    centers.push(far.vec);
  }

  let assignments = new Array(points.length).fill(0);
  for (let iter = 0; iter < KMEANS_ITERATIONS; iter++) {
    const next = points.map((p) => {
      let best = 0;
      let bestSim = -Infinity;
      centers.forEach((c, ci) => {
        const sim = cosine(p.vec, c);
        if (sim > bestSim) {
          bestSim = sim;
          best = ci;
        }
      });
      return best;
    });
    const changed = next.some((a, i) => a !== assignments[i]);
    assignments = next;
    for (let ci = 0; ci < centers.length; ci++) {
      const members = points.filter((_, i) => assignments[i] === ci);
      if (members.length) centers[ci] = weightedCentroid(members);
    }
    if (!changed && iter > 0) break;
  }
  return assignments;
}
