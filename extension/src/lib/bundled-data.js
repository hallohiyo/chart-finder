// 확장 프로그램에 내장된 주제 데이터를 읽는다 (extension/data/, scripts/build-topics.mjs 가 만든다).
import { decodeVectors } from './quantize.js';

export async function loadBundledTopics(urlFor = (p) => chrome.runtime.getURL(p), fetchImpl = fetch) {
  const [metaRes, vecRes] = await Promise.all([fetchImpl(urlFor('data/topics.json')), fetchImpl(urlFor('data/topic-vectors.bin'))]);
  if (!metaRes.ok || !vecRes.ok) {
    throw new Error('내장 주제 데이터가 없어요. update.bat 으로 최신 버전을 받아 주세요.');
  }
  const { meta, topics } = await metaRes.json();
  const vectors = decodeVectors(await vecRes.arrayBuffer(), meta.count, meta.dim);
  return { meta, topics, vectors };
}
