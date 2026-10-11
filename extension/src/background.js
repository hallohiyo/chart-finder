import { createStore } from './lib/store.js';
import { createService } from './lib/service.js';
import { parseWatchUrl, cleanPageTitle } from './lib/youtube.js';

const HISTORY_IMPORT_DAYS = 90;
const YOUTUBE_ORIGIN = 'https://www.youtube.com';

const store = createStore(chrome.storage.local);
const service = createService(store);

chrome.runtime.onInstalled.addListener(({ reason }) => {
  chrome.alarms.create('enrich', { periodInMinutes: 60 });
  if (reason === 'install') chrome.runtime.openOptionsPage();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'enrich' || alarm.name === 'enrich-soon') enrich();
});

async function enrich() {
  try {
    return await service.enrichPending();
  } catch (err) {
    // 할당량 초과/네트워크 오류: 다음 주기에 다시 시도한다
    console.warn('[살짝 빗나간] 메타데이터 조회 실패:', err.message);
    return { enriched: 0, error: err.message };
  }
}

function isExtensionPage(sender) {
  return sender.id === chrome.runtime.id && (sender.url ?? '').startsWith(chrome.runtime.getURL(''));
}

function isYoutubeContentScript(sender) {
  return sender.id === chrome.runtime.id && sender.tab && !sender.tab.incognito && sender.origin === YOUTUBE_ORIGIN;
}

async function handle(msg, sender) {
  if (msg?.type === 'watch') {
    if (!isYoutubeContentScript(sender)) return { recorded: false };
    const result = await service.recordWatch(msg);
    if (result.needsEnrich) chrome.alarms.create('enrich-soon', { delayInMinutes: 0.5 });
    return result;
  }

  // 나머지 메시지는 팝업/설정 페이지에서만 받는다
  if (!isExtensionPage(sender)) throw new Error('forbidden');
  switch (msg?.type) {
    case 'getToday':
      return service.getToday();
    case 'next':
      return service.next();
    case 'react':
      return service.react(msg.topicId, msg.reaction);
    case 'overview':
      return service.overview();
    case 'saveSettings': {
      const { apiKey, paused, maxPerDay } = msg.settings ?? {};
      const patch = Object.fromEntries(
        Object.entries({ apiKey, paused, maxPerDay }).filter(([, v]) => v !== undefined),
      );
      const settings = await store.updateSettings(patch);
      if (settings.apiKey) await enrich();
      return service.overview();
    }
    case 'enrichNow':
      return enrich();
    case 'importHistory':
      return importHistory();
    case 'clearData':
      await store.clearData();
      return service.overview();
    default:
      throw new Error(`unknown message: ${msg?.type}`);
  }
}

/** 선택 권한(history)이 허용된 경우에만 동작. youtube.com/watch 기록만 골라 로컬에 저장한다 */
async function importHistory() {
  if (!chrome.history) throw new Error('방문 기록 권한이 없습니다.');
  const items = await chrome.history.search({
    text: 'youtube.com/watch',
    startTime: Date.now() - HISTORY_IMPORT_DAYS * 86400000,
    maxResults: 5000,
  });
  const byId = new Map();
  for (const item of items) {
    const videoId = parseWatchUrl(item.url);
    if (!videoId || byId.has(videoId)) continue;
    byId.set(videoId, { videoId, title: cleanPageTitle(item.title), at: item.lastVisitTime });
  }
  const result = await service.importHistory([...byId.values()]);
  await enrich();
  return result;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handle(msg, sender).then(
    (data) => sendResponse({ ok: true, data }),
    (err) => sendResponse({ ok: false, error: err.message }),
  );
  return true;
});
