// 추천 후보 주제 목록: 영어 위키백과 "Vital articles Level 4" (편집자들이 고른 핵심 문서 약 1만 개)를
// 한국어 위키백과 문서로 연결해서 쓴다. 모든 요청은 사용자 브라우저에서 MediaWiki API 로 보낸다.

const EN_API = 'https://en.wikipedia.org/w/api.php';
const KO_API = 'https://ko.wikipedia.org/w/api.php';
const KO_REST = 'https://ko.wikipedia.org/api/rest_v1';
const VITAL_ROOT = 'Wikipedia:Vital articles/Level/4';
const BATCH = 50; // MediaWiki titles= 파라미터 최대 개수

// 사람·장소 목록은 "지식 주제"보다 인명사전/지명사전에 가까워서 기본으로 뺀다.
export const DEFAULT_SECTIONS = [
  'Arts',
  'Philosophy and religion',
  'Everyday life',
  'Society and social sciences',
  'Biological and health sciences',
  'Physical sciences',
  'Technology',
  'Mathematics',
  'History',
];

function apiUrl(base, params) {
  const q = new URLSearchParams({ format: 'json', formatversion: '2', origin: '*', ...params });
  return `${base}?${q}`;
}

async function getJson(fetchImpl, url) {
  const res = await fetchImpl(url, { headers: { 'Api-User-Agent': 'slightly-off-recommender/0.2 (personal Chrome extension)' } });
  if (!res.ok) throw new Error(`위키백과 요청 실패 (${res.status})`);
  const body = await res.json();
  if (body.error) throw new Error(`위키백과 API 오류: ${body.error.info ?? body.error.code}`);
  return body;
}

/** action=query 를 continue 토큰이 없어질 때까지 반복한다 */
async function queryAll(fetchImpl, base, params) {
  const pages = [];
  let cont = {};
  for (let guard = 0; guard < 200; guard++) {
    const body = await getJson(fetchImpl, apiUrl(base, { action: 'query', ...params, ...cont }));
    pages.push(...(body.query?.pages ?? []));
    if (!body.continue) break;
    cont = body.continue;
  }
  return pages;
}

/**
 * Vital articles 목록 페이지(와 하위 페이지)에 걸린 일반 문서 제목을 모은다.
 * 섹션 페이지가 다시 하위 페이지로 나뉘어 있으면 따라 들어간다.
 */
export async function fetchVitalTitles(fetchImpl, sections = DEFAULT_SECTIONS, onProgress = () => {}) {
  const titles = new Set();
  const visited = new Set();
  const queue = sections.map((s) => `${VITAL_ROOT}/${s}`);
  while (queue.length) {
    const page = queue.shift();
    if (visited.has(page)) continue;
    visited.add(page);
    const pages = await queryAll(fetchImpl, EN_API, { prop: 'links', titles: page, plnamespace: '0|4', pllimit: 'max' });
    for (const p of pages) {
      for (const link of p.links ?? []) {
        if (link.ns === 0) titles.add(link.title);
        else if (link.ns === 4 && link.title.startsWith(`${page}/`)) queue.push(link.title);
      }
    }
    onProgress({ pages: visited.size, titles: titles.size });
  }
  return [...titles];
}

/** 영어 문서 제목 → 한국어 문서 제목. 한국어판이 없는 문서는 버린다 */
export async function mapToKorean(fetchImpl, enTitles, onProgress = () => {}) {
  const out = [];
  const seenKo = new Set();
  for (let i = 0; i < enTitles.length; i += BATCH) {
    const batch = enTitles.slice(i, i + BATCH);
    const pages = await queryAll(fetchImpl, EN_API, {
      prop: 'langlinks',
      lllang: 'ko',
      lllimit: 'max',
      redirects: '1',
      titles: batch.join('|'),
    });
    for (const p of pages) {
      const ko = p.langlinks?.find((l) => l.lang === 'ko');
      const koTitle = ko?.title ?? ko?.['*'];
      if (!koTitle || seenKo.has(koTitle)) continue;
      seenKo.add(koTitle);
      out.push({ id: p.title, title: koTitle });
    }
    onProgress({ done: Math.min(i + BATCH, enTitles.length), total: enTitles.length });
  }
  return out;
}

/** 한국어 문서마다 짧은 설명(위키데이터 설명)을 붙인다. 임베딩 문장을 풍부하게 하려는 용도 */
export async function addKoreanDescriptions(fetchImpl, topics, onProgress = () => {}) {
  const byTitle = new Map(topics.map((t) => [t.title, t]));
  for (let i = 0; i < topics.length; i += BATCH) {
    const batch = topics.slice(i, i + BATCH);
    const body = await getJson(
      fetchImpl,
      apiUrl(KO_API, { action: 'query', prop: 'description', redirects: '1', titles: batch.map((t) => t.title).join('|') }),
    );
    // 정규화/넘겨주기로 제목이 바뀐 경우 원래 제목으로 되돌려 찾는다
    const renamed = new Map();
    for (const r of [...(body.query?.normalized ?? []), ...(body.query?.redirects ?? [])]) renamed.set(r.to, r.from);
    for (const p of body.query?.pages ?? []) {
      let title = p.title;
      while (!byTitle.has(title) && renamed.has(title)) title = renamed.get(title);
      const t = byTitle.get(title);
      if (t && p.description) t.description = p.description;
    }
    onProgress({ done: Math.min(i + BATCH, topics.length), total: topics.length });
  }
  return topics;
}

/** 팝업에 보여줄 요약과 링크 */
export async function fetchSummary(fetchImpl, koTitle) {
  const url = `${KO_REST}/page/summary/${encodeURIComponent(koTitle.replaceAll(' ', '_'))}`;
  try {
    const res = await fetchImpl(url, { headers: { 'Api-User-Agent': 'slightly-off-recommender/0.2 (personal Chrome extension)' } });
    if (!res.ok) throw new Error(String(res.status));
    const body = await res.json();
    return {
      extract: body.extract ?? '',
      url: body.content_urls?.desktop?.page ?? wikiUrl(koTitle),
    };
  } catch {
    return { extract: '', url: wikiUrl(koTitle) };
  }
}

export function wikiUrl(koTitle) {
  return `https://ko.wikipedia.org/wiki/${encodeURIComponent(koTitle.replaceAll(' ', '_'))}`;
}

export function youtubeSearchUrl(query) {
  return `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`;
}

/** 임베딩에 넣을 문장 */
export function topicText(topic) {
  return topic.description ? `${topic.title}: ${topic.description}` : topic.title;
}
