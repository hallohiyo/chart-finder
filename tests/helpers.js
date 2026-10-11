// chrome.storage.local 과 같은 인터페이스의 메모리 저장소
export function memoryStorage(initial = {}) {
  const data = structuredClone(initial);
  return {
    data,
    async get(key) {
      const keys = Array.isArray(key) ? key : [key];
      return Object.fromEntries(keys.filter((k) => k in data).map((k) => [k, structuredClone(data[k])]));
    },
    async set(obj) {
      for (const [k, v] of Object.entries(obj)) data[k] = structuredClone(v);
    },
    async remove(keys) {
      for (const k of [].concat(keys)) delete data[k];
    },
  };
}

// IndexedDB kv 대용
export function memoryKv() {
  const m = new Map();
  return {
    map: m,
    get: async (k) => (m.has(k) ? structuredClone(m.get(k)) : undefined),
    set: async (k, v) => void m.set(k, structuredClone(v)),
    delete: async (k) => void m.delete(k),
  };
}

/** 단위 벡터: 첫 두 축 평면에서 angle(라디안) 방향 */
export function angleVec(angle, dim = 8) {
  const v = new Float32Array(dim);
  v[0] = Math.cos(angle);
  v[1] = Math.sin(angle);
  return v;
}

/** 단어 가방 해시 임베딩: 단어가 겹칠수록 가깝다. 실제 모델 대신 테스트에서 쓴다 */
export const FAKE_DIM = 64;
export async function fakeEmbed(texts) {
  return texts.map((t) => {
    const v = new Float32Array(FAKE_DIM);
    for (const w of t.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean)) {
      let h = 0;
      for (const ch of w) h = (h * 31 + ch.codePointAt(0)) >>> 0;
      v[h % FAKE_DIM] += 1;
    }
    let n = Math.hypot(...v) || 1;
    return v.map((x) => x / n);
  });
}

const DAY = 86400000;

export function videos(titles, { now = Date.now(), prefix = 'v' } = {}) {
  return titles.map((title, i) => ({
    id: `${prefix}${String(i).padStart(10, '0')}`.slice(0, 11),
    title,
    description: '',
    tags: [],
    categoryId: null,
    firstSeen: now - i * DAY,
    lastSeen: now - i * DAY,
    count: 1,
    enriched: true,
  }));
}

/**
 * 가짜 MediaWiki API. 실제 응답 모양(formatversion=2)을 흉내 낸다.
 * vital: 페이지 → 링크, redirects: 넘겨주기, categories: 분류 → 구성원
 */
export function fakeWikiFetch({ vital = {}, redirects = {}, categories = {}, langlinks = {}, descriptions = {}, summaries = {} }) {
  const calls = [];
  const json = (body, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => body });
  const impl = async (url) => {
    calls.push(url);
    const u = new URL(url);
    const p = u.searchParams;
    if (u.pathname.startsWith('/api/rest_v1/page/summary/')) {
      const title = decodeURIComponent(u.pathname.split('/').pop()).replaceAll('_', ' ');
      return summaries[title] ? json(summaries[title]) : json({}, false);
    }
    if (p.get('list') === 'categorymembers') {
      return json({ query: { categorymembers: categories[p.get('cmtitle')] ?? [] } });
    }
    const titles = (p.get('titles') ?? '').split('|');
    if (p.get('prop') === 'links') {
      const asked = titles[0];
      const target = p.get('redirects') && redirects[asked] ? redirects[asked] : asked;
      const ns = p.get('plnamespace').split('|').map(Number);
      if (!(target in vital)) return json({ query: { pages: [{ title: target, missing: true }] } });
      const links = vital[target].filter((l) => ns.includes(l.ns));
      return json({ query: { ...(target !== asked ? { redirects: [{ from: asked, to: target }] } : {}), pages: [{ title: target, links }] } });
    }
    if (p.get('prop') === 'langlinks') {
      return json({
        query: {
          pages: titles.map((t) => ({ title: t, ...(langlinks[t] ? { langlinks: [{ lang: 'ko', title: langlinks[t] }] } : {}) })),
        },
      });
    }
    if (p.get('prop') === 'description') {
      return json({ query: { pages: titles.map((t) => ({ title: t, ...(descriptions[t] ? { description: descriptions[t] } : {}) })) } });
    }
    throw new Error(`unexpected request ${url}`);
  };
  return { impl, calls };
}
