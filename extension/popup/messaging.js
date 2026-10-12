// 팝업·설정 페이지 → 백그라운드 메시지.
//
// 업데이트로 파일이 바뀌면 페이지는 바로 새 버전을 읽지만, 백그라운드(서비스 워커)는 확장 프로그램을
// 다시 불러오기 전까지 옛 코드로 돈다. 그러면 새 요청을 "unknown message" 로 거절한다.
// 사용자에게 ↻ 를 누르라고 하는 대신, 버전이 다르면 페이지가 직접 확장 프로그램을 다시 불러온다.

import { CODE_VERSION } from '../src/version.js';

const RELOAD_GUARD_MS = 60 * 1000; // 다시 불러와도 안 고쳐지면 무한 반복하지 않도록

export const PAGE_VERSION = CODE_VERSION;

async function raw(type, payload = {}) {
  return chrome.runtime.sendMessage({ type, ...payload });
}

/** 백그라운드 버전이 이 페이지와 다르면 확장 프로그램을 다시 불러온다. 다시 불러오면 이 페이지는 닫힌다 */
export async function ensureFreshBackground(reopen) {
  let bg = null;
  try {
    const res = await raw('ping');
    bg = res?.ok ? res.data.version : null;
  } catch {
    // 백그라운드가 아직 안 깨어났으면 다음 요청에서 다시 확인한다
    return { page: PAGE_VERSION, background: null };
  }
  if (bg !== PAGE_VERSION) await reloadExtension(reopen, `background ${bg ?? '(구버전)'} ≠ page ${PAGE_VERSION}`);
  return { page: PAGE_VERSION, background: bg };
}

async function reloadExtension(reopen, reason) {
  const { lastSelfReload = 0 } = await chrome.storage.local.get('lastSelfReload');
  if (Date.now() - lastSelfReload < RELOAD_GUARD_MS) {
    throw new Error(`확장 프로그램을 다시 불러왔는데도 버전이 맞지 않아요 (${reason}). chrome://extensions 에서 이 확장 프로그램이 두 개 설치돼 있지 않은지, 로드한 폴더가 업데이트한 폴더가 맞는지 확인해 주세요.`);
  }
  await chrome.storage.local.set({ lastSelfReload: Date.now(), reopenAfterReload: reopen ?? null });
  chrome.runtime.reload();
  // 여기서 페이지가 닫힌다
  await new Promise(() => {});
}

/** 요청을 보내고 결과를 돌려준다. 백그라운드가 옛 버전이라 모르는 요청이면 다시 불러온다 */
export async function send(type, payload = {}, { reopen } = {}) {
  const res = await raw(type, payload);
  if (!res?.ok) {
    if (res?.error?.startsWith('unknown message')) await reloadExtension(reopen, res.error);
    throw new Error(res?.error ?? '알 수 없는 오류');
  }
  return res.data;
}
