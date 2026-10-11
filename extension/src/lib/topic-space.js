// 주제 공간 맞추기: 모든 벡터에서 "주제 벡터 평균"을 뺀 뒤 다시 정규화한다 (중심화).
//
// e5 계열 임베딩은 모든 문장이 큰 공통 방향을 공유해서 유사도가 0.90~0.91 에 몰린다.
// 그 상태로 순위를 매기면 상위 1% 밖은 뜻보다 글자 수 같은 잡음이 순서를 정한다
// (실제 데이터로 확인: 음악 → 상위 3% 에 "관목", "십자말"). 평균을 빼면 주제마다 다른 부분만 남아
// 상위 몇 % 까지 뜻이 이어진다 (음악 → 상위 1% 에 "펑크", "녹음과 재생").
// 영상 벡터도 같은 평균을 빼야 같은 공간에서 비교된다.

import { normalize } from './vector.js';

/** 내장 데이터 → 추천에 쓰는 인덱스. vectors 는 제자리에서 중심화된다 */
export function prepareIndex({ meta, topics, vectors }) {
  const dim = meta.dim;
  const n = vectors.length / dim;
  const mean = new Float32Array(dim);
  for (let i = 0; i < n; i++) for (let j = 0; j < dim; j++) mean[j] += vectors[i * dim + j] / n;
  for (let i = 0; i < n; i++) normalize(subtractInto(vectors.subarray(i * dim, (i + 1) * dim), mean));
  return { meta, topics, vectors, dim, mean };
}

/** 모델이 낸 벡터(영상 등) → 주제와 같은 공간의 벡터 (새 배열) */
export function toTopicSpace(vec, index) {
  return normalize(subtractInto(Float32Array.from(vec), index.mean));
}

function subtractInto(v, mean) {
  for (let j = 0; j < v.length; j++) v[j] -= mean[j];
  return v;
}
