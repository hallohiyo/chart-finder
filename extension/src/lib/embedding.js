// 로컬 개념 임베딩 (Explicit Semantic Analysis 계열).
// 텍스트를 "도메인 사전"과 매칭해 도메인 차원의 벡터로 만들고,
// 도메인 연관 그래프로 한 번 스무딩해 이웃 분야끼리 거리가 가까워지도록 한다.
// 외부 모델/네트워크 없이 브라우저 안에서 모두 계산된다.

import { DOMAINS, DOMAIN_RELATIONS, YT_CATEGORY_DOMAINS } from '../data/domains.js';

export const DIM = DOMAINS.length;
export const DOMAIN_INDEX = Object.fromEntries(DOMAINS.map((d, i) => [d.id, i]));

const SMOOTHING = 0.35;
const MAX_HITS_PER_KEYWORD = 3;
const FIELD_WEIGHTS = { title: 2, tags: 2, description: 0.6 };
const CATEGORY_WEIGHT = 1.5;

// 관계 행렬 R (DIM x DIM, 희소 → 인접 리스트)
const NEIGHBORS = DOMAINS.map(() => []);
for (const [a, b, w] of DOMAIN_RELATIONS) {
  const ia = DOMAIN_INDEX[a];
  const ib = DOMAIN_INDEX[b];
  if (ia === undefined || ib === undefined) throw new Error(`unknown domain in relation: ${a}-${b}`);
  NEIGHBORS[ia].push([ib, w]);
  NEIGHBORS[ib].push([ia, w]);
}

// 키워드 매처. 여러 도메인에 걸친 키워드는 가중치를 나눠 갖는다 (IDF 유사).
const KEYWORDS = buildKeywordTable();

function buildKeywordTable() {
  const owners = new Map();
  DOMAINS.forEach((d, i) => {
    for (const kw of d.keywords) {
      const key = kw.toLowerCase();
      if (!owners.has(key)) owners.set(key, []);
      owners.get(key).push(i);
    }
  });
  return [...owners.entries()].map(([kw, idx]) => ({
    kw,
    domains: idx,
    weight: 1 / idx.length,
    // 영문/숫자 키워드는 단어 경계로 매칭 (예: "ai"가 "said"에 걸리지 않도록)
    regex: /^[a-z0-9 .\-]+$/.test(kw)
      ? new RegExp(`(?:^|[^a-z0-9])${escapeRegex(kw)}(?=$|[^a-z0-9])`, 'g')
      : null,
  }));
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function countHits(text, entry) {
  if (!text) return 0;
  if (entry.regex) {
    entry.regex.lastIndex = 0;
    let n = 0;
    while (entry.regex.exec(text) && n < MAX_HITS_PER_KEYWORD) n++;
    return n;
  }
  let n = 0;
  let pos = text.indexOf(entry.kw);
  while (pos !== -1 && n < MAX_HITS_PER_KEYWORD) {
    n++;
    pos = text.indexOf(entry.kw, pos + entry.kw.length);
  }
  return n;
}

/** 텍스트 필드 → 스무딩 전 원시 도메인 벡터 */
export function rawVectorFromText({ title = '', description = '', tags = [], categoryId } = {}) {
  const v = new Float64Array(DIM);
  const fields = {
    title: title.toLowerCase(),
    tags: tags.join(' | ').toLowerCase(),
    description: description.toLowerCase().slice(0, 1500),
  };
  for (const entry of KEYWORDS) {
    for (const [field, fw] of Object.entries(FIELD_WEIGHTS)) {
      const hits = countHits(fields[field], entry);
      if (!hits) continue;
      // 같은 키워드가 여러 번 나와도 로그 스케일로만 증가
      const score = fw * entry.weight * (1 + Math.log(hits));
      for (const i of entry.domains) v[i] += score;
    }
  }
  const cat = categoryId != null ? YT_CATEGORY_DOMAINS[String(categoryId)] : null;
  if (cat) {
    for (const [id, w] of Object.entries(cat)) v[DOMAIN_INDEX[id]] += CATEGORY_WEIGHT * w;
  }
  return v;
}

/** {domainId: weight} → 원시 벡터 */
export function rawVectorFromDomains(weights) {
  const v = new Float64Array(DIM);
  for (const [id, w] of Object.entries(weights)) {
    const i = DOMAIN_INDEX[id];
    if (i === undefined) throw new Error(`unknown domain: ${id}`);
    v[i] += w;
  }
  return v;
}

/** 원시 벡터 → 정규화 + 관계 스무딩된 임베딩. 신호가 없으면 null */
export function finalize(raw) {
  const n0 = norm(raw);
  if (n0 === 0) return null;
  const base = raw.map((x) => x / n0);
  const out = Float64Array.from(base);
  for (let i = 0; i < DIM; i++) {
    if (!base[i]) continue;
    for (const [j, w] of NEIGHBORS[i]) out[j] += SMOOTHING * w * base[i];
  }
  return normalize(out);
}

export function embedVideo(video) {
  return finalize(rawVectorFromText(video));
}

export function embedTopic(topic) {
  return finalize(rawVectorFromDomains(topic.domains));
}

export function norm(v) {
  let s = 0;
  for (const x of v) s += x * x;
  return Math.sqrt(s);
}

export function normalize(v) {
  const n = norm(v);
  return n === 0 ? v : v.map((x) => x / n);
}

export function cosine(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

/** 코사인 거리 (0 = 같음, 1 = 무관). 모든 성분이 0 이상이라 [0, 1] 범위 */
export function distance(a, b) {
  return Math.max(0, Math.min(1, 1 - cosine(a, b)));
}

/** 벡터에서 비중이 큰 도메인 id 상위 n개 */
export function topDomains(v, n = 3) {
  return [...v]
    .map((w, i) => [DOMAINS[i].id, w])
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .filter(([, w]) => w > 0);
}
