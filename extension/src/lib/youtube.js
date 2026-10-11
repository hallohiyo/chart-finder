// YouTube URL 파싱과 Data API v3 조회.

const WATCH_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com']);
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const API_ENDPOINT = 'https://www.googleapis.com/youtube/v3/videos';
const MAX_IDS_PER_REQUEST = 50;
const DESCRIPTION_LIMIT = 1000;

/** youtube.com/watch?v=ID 형태만 인정한다. 그 외에는 null */
export function parseWatchUrl(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
  if (!WATCH_HOSTS.has(u.hostname) || u.pathname !== '/watch') return null;
  const id = u.searchParams.get('v');
  return id && VIDEO_ID.test(id) ? id : null;
}

export function isValidVideoId(id) {
  return typeof id === 'string' && VIDEO_ID.test(id);
}

/** 브라우저 탭 제목 "영상 제목 - YouTube" → "영상 제목" */
export function cleanPageTitle(title) {
  return String(title ?? '')
    .replace(/^\(\d+\)\s*/, '')
    .replace(/\s*-\s*YouTube\s*$/, '')
    .trim();
}

/**
 * 영상 ID 목록의 메타데이터를 조회한다.
 * @returns {Promise<Map<string, {title, description, tags, categoryId, channelTitle}>>}
 */
export async function fetchVideoMetadata(ids, apiKey, fetchImpl = fetch) {
  const result = new Map();
  for (let i = 0; i < ids.length; i += MAX_IDS_PER_REQUEST) {
    const batch = ids.slice(i, i + MAX_IDS_PER_REQUEST);
    const url = `${API_ENDPOINT}?part=snippet&id=${batch.join(',')}&key=${encodeURIComponent(apiKey)}`;
    const res = await fetchImpl(url);
    if (!res.ok) {
      let reason = '';
      try {
        reason = (await res.json())?.error?.errors?.[0]?.reason ?? '';
      } catch {
        // 응답 본문이 JSON이 아니면 무시
      }
      const err = new Error(`YouTube API ${res.status}${reason ? ` (${reason})` : ''}`);
      err.status = res.status;
      err.reason = reason;
      throw err;
    }
    const body = await res.json();
    for (const item of body.items ?? []) {
      const s = item.snippet ?? {};
      result.set(item.id, {
        title: s.title ?? '',
        description: (s.description ?? '').slice(0, DESCRIPTION_LIMIT),
        tags: (s.tags ?? []).slice(0, 30),
        categoryId: s.categoryId ?? null,
        channelTitle: s.channelTitle ?? '',
      });
    }
  }
  return result;
}
