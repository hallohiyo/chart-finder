// 추천 후보 주제 목록: 영어 위키백과 "Vital articles Level 4" (편집자들이 고른 핵심 문서 약 1만 개)를
// 한국어 위키백과 문서로 연결해서 쓴다. 모든 요청은 사용자 브라우저에서 MediaWiki API 로 보낸다.

const EN_API = 'https://en.wikipedia.org/w/api.php';
const KO_API = 'https://ko.wikipedia.org/w/api.php';
const KO_REST = 'https://ko.wikipedia.org/api/rest_v1';
const VITAL_ROOT = 'Wikipedia:Vital articles/Level/4';
const BATCH = 50; // MediaWiki titles= 파라미터 최대 개수

const VITAL_CATEGORY = 'Category:Wikipedia level-4 vital articles';
const MIN_TITLES = 1000; // 목록 페이지에서 이보다 적게 나오면 분류(category)에서도 모은다

// 사람·장소 목록은 "지식 주제"보다 인명사전/지명사전에 가까워서 뺀다.
export const EXCLUDED_SECTIONS = ['People', 'Geography'];

// 상위 페이지에서 섹션을 못 찾았을 때 쓰는 예비 이름
export const FALLBACK_SECTIONS = [
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

const RETRIES = 6;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 429(요청 과다)·5xx 는 잠깐 쉬었다 다시 시도한다. Retry-After 가 있으면 그만큼 기다린다 */
async function getJson(fetchImpl, url, { wait = sleep } = {}) {
  for (let attempt = 0; ; attempt++) {
    const res = await fetchImpl(url, { headers: { 'Api-User-Agent': 'slightly-off-recommender/0.2 (personal Chrome extension)' } });
    if (res.ok) {
      const body = await res.json();
      if (body.error?.code === 'maxlag' && attempt < RETRIES) {
        await wait(5000);
        continue;
      }
      if (body.error) throw new Error(`위키백과 API 오류: ${body.error.info ?? body.error.code}`);
      return body;
    }
    const retryable = res.status === 429 || res.status >= 500;
    if (!retryable || attempt >= RETRIES) throw new Error(`위키백과 요청 실패 (${res.status})`);
    const after = Number(res.headers?.get?.('retry-after'));
    await wait(Number.isFinite(after) && after > 0 ? after * 1000 : 2000 * 2 ** attempt);
  }
}

/** 빌드 스크립트용: 요청 사이에 최소 간격을 둔다 (위키미디어 API 예절) */
export function paced(fetchImpl, minGapMs) {
  let next = 0;
  return async (url, init) => {
    const now = Date.now();
    const at = Math.max(now, next);
    next = at + minGapMs;
    if (at > now) await sleep(at - now);
    return fetchImpl(url, init);
  };
}

export const _test = { getJson };

/** action=query 를 continue 토큰이 없어질 때까지 반복한다. key: query 안에서 모을 배열 이름 */
async function queryAll(fetchImpl, base, params, key = 'pages') {
  const items = [];
  let cont = {};
  for (let guard = 0; guard < 500; guard++) {
    const body = await getJson(fetchImpl, apiUrl(base, { action: 'query', ...params, ...cont }));
    items.push(...(body.query?.[key] ?? []));
    if (!body.continue) break;
    cont = body.continue;
  }
  return items;
}

/** 페이지에 걸린 링크. 넘겨주기(redirect)는 따라간다. @returns {page: 실제 제목, links} 배열 */
async function linksOf(fetchImpl, title, namespaces) {
  const pages = await queryAll(fetchImpl, EN_API, { prop: 'links', titles: title, plnamespace: namespaces, pllimit: 'max', redirects: '1' });
  const byTitle = new Map();
  for (const p of pages) {
    if (!byTitle.has(p.title)) byTitle.set(p.title, []);
    byTitle.get(p.title).push(...(p.links ?? []));
  }
  return [...byTitle].map(([page, links]) => ({ page, links }));
}

const isExcluded = (name) => EXCLUDED_SECTIONS.some((x) => name.includes(x));

/** 방법 1: 목록 페이지. 상위 페이지에서 섹션을 찾고, 섹션의 하위 페이지까지 따라간다 */
async function fromListPages(fetchImpl, onProgress) {
  const root = await linksOf(fetchImpl, VITAL_ROOT, '4');
  const rootTitle = root[0]?.page ?? VITAL_ROOT;
  let sections = (root[0]?.links ?? [])
    .map((l) => l.title)
    .filter((t) => t.startsWith(`${rootTitle}/`) && !t.slice(rootTitle.length + 1).includes('/'))
    .filter((t) => !isExcluded(t.slice(rootTitle.length + 1)));
  if (sections.length === 0) sections = FALLBACK_SECTIONS.map((s) => `${VITAL_ROOT}/${s}`);

  const titles = new Set();
  const visited = new Set();
  const queue = [...sections];
  while (queue.length && visited.size < 300) {
    const page = queue.shift();
    if (visited.has(page)) continue;
    visited.add(page);
    for (const { page: actual, links } of await linksOf(fetchImpl, page, '0|4')) {
      visited.add(actual);
      for (const link of links) {
        if (link.ns === 0) titles.add(link.title);
        else if (link.ns === 4 && link.title.startsWith(`${actual}/`)) queue.push(link.title);
      }
    }
    onProgress({ titles: titles.size });
  }
  return { titles, sections: sections.length, pages: visited.size };
}

/** 방법 2: 분류. 토론 문서(Talk:)에 붙은 분류라서 "Talk:" 를 떼면 문서 제목이 된다 */
async function fromCategory(fetchImpl, onProgress, seed = new Set()) {
  const titles = new Set(seed);
  const queue = [{ cat: VITAL_CATEGORY, depth: 0 }];
  const visited = new Set();
  while (queue.length && visited.size < 100) {
    const { cat, depth } = queue.shift();
    if (visited.has(cat)) continue;
    visited.add(cat);
    const members = await queryAll(
      fetchImpl,
      EN_API,
      { list: 'categorymembers', cmtitle: cat, cmtype: 'page|subcat', cmlimit: 'max' },
      'categorymembers',
    );
    for (const m of members) {
      if (m.ns === 14) {
        if (depth < 2 && !isExcluded(m.title)) queue.push({ cat: m.title, depth: depth + 1 });
      } else if (m.ns === 1) titles.add(m.title.replace(/^Talk:/, ''));
      else if (m.ns === 0) titles.add(m.title);
    }
    onProgress({ titles: titles.size });
  }
  return { titles, categories: visited.size };
}

/**
 * Vital articles Level 4 의 문서 제목을 모은다. 페이지 이름·구조가 바뀌어도 되도록
 * 목록 페이지 → (부족하면) 분류 순서로 시도하고, 둘 다 실패하면 어디서 몇 개 나왔는지 알려준다.
 */
export async function fetchVitalTitles(fetchImpl, onProgress = () => {}) {
  const diag = [];
  let titles = new Set();
  try {
    const r = await fromListPages(fetchImpl, onProgress);
    titles = r.titles;
    diag.push(`목록 페이지 ${r.pages}개에서 ${r.titles.size}개`);
  } catch (err) {
    diag.push(`목록 페이지 실패: ${err.message}`);
  }
  if (titles.size < MIN_TITLES) {
    try {
      const r = await fromCategory(fetchImpl, onProgress, titles);
      diag.push(`분류 ${r.categories}개까지 합쳐 ${r.titles.size}개`);
      titles = r.titles;
    } catch (err) {
      diag.push(`분류 실패: ${err.message}`);
    }
  }
  return { titles: [...titles], diagnostics: diag.join(', ') };
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
  await fillFromKoreanPages(fetchImpl, topics, BATCH, { prop: 'description' }, (t, p) => {
    if (p.description) t.description = p.description;
  }, onProgress);
  return topics;
}

const EXTRACT_BATCH = 20; // prop=extracts 의 exintro 는 한 번에 20개까지
const EXTRACT_MAX_CHARS = 120;

/**
 * 짧은 설명이 없는 주제에 문서 첫 문장을 붙인다.
 * 설명 없이 제목만 임베딩하면 짧은 영상 제목과 "형식"이 닮아서, 뜻과 상관없이 가깝게 나온다
 * (실제로 "바가바드 기타", "가우스 소거법"이 여러 관심사 추천에 계속 끼어들었다).
 */
export async function addKoreanExtracts(fetchImpl, topics, onProgress = () => {}) {
  const missing = topics.filter((t) => !t.description);
  await fillFromKoreanPages(
    fetchImpl,
    missing,
    EXTRACT_BATCH,
    { prop: 'extracts', exintro: '1', explaintext: '1', exsentences: '1', exlimit: String(EXTRACT_BATCH) },
    (t, p) => {
      const text = (p.extract ?? '').replace(/\s+/g, ' ').trim();
      if (text) t.description = text.length > EXTRACT_MAX_CHARS ? `${text.slice(0, EXTRACT_MAX_CHARS)}…` : text;
    },
    onProgress,
  );
  return topics;
}

/** 한국어 문서 정보를 batch 개씩 조회해 apply(topic, page) 로 채운다. 정규화·넘겨주기된 제목도 원래 주제에 맞춘다 */
async function fillFromKoreanPages(fetchImpl, topics, batchSize, params, apply, onProgress) {
  const byTitle = new Map(topics.map((t) => [t.title, t]));
  for (let i = 0; i < topics.length; i += batchSize) {
    const batch = topics.slice(i, i + batchSize);
    const body = await getJson(
      fetchImpl,
      apiUrl(KO_API, { action: 'query', redirects: '1', ...params, titles: batch.map((t) => t.title).join('|') }),
    );
    const renamed = new Map();
    for (const r of [...(body.query?.normalized ?? []), ...(body.query?.redirects ?? [])]) renamed.set(r.to, r.from);
    for (const p of body.query?.pages ?? []) {
      let title = p.title;
      while (!byTitle.has(title) && renamed.has(title)) title = renamed.get(title);
      const t = byTitle.get(title);
      if (t) apply(t, p);
    }
    onProgress({ done: Math.min(i + batchSize, topics.length), total: topics.length });
  }
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
