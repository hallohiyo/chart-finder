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

const DAY = 86400000;

export function videos(titles, { categoryId = null, now = Date.now(), prefix = 'v' } = {}) {
  return titles.map((title, i) => ({
    id: `${prefix}${String(i).padStart(10, '0')}`.slice(0, 11),
    title,
    description: '',
    tags: [],
    categoryId,
    firstSeen: now - i * DAY,
    lastSeen: now - i * DAY,
    count: 1,
    enriched: true,
  }));
}

export const MUSIC_TITLES = [
  '아이유 노래 라이브 클립',
  '재즈 피아노 플레이리스트',
  '기타 코드 진행 강좌',
  '밴드 커버 공연',
  '힙합 앨범 리뷰',
  '오케스트라 클래식 명곡',
];

export const GAME_TITLES = [
  '젤다 보스전 공략',
  '마인크래프트 생존 1화',
  '닌텐도 신작 게임',
  '포켓몬 게임 플레이',
  'fps 게임 매드무비',
  '인디게임 추천',
];
