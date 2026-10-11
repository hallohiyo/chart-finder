// youtube.com 에서만 실행된다. 시청 페이지(/watch?v=)에 일정 시간 머문 경우에만
// 영상 ID와 제목을 확장 프로그램 내부(백그라운드)로 보낸다. 페이지 내용은 그 외에 읽지 않는다.
(() => {
  if (chrome.extension?.inIncognitoContext) return;

  const DWELL_MS = 15000; // 잠깐 스쳐 지나간 영상은 관심사로 치지 않는다
  const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
  let timer = null;
  let lastSent = null;

  function currentVideoId() {
    if (location.pathname !== '/watch') return null;
    const id = new URLSearchParams(location.search).get('v');
    return id && VIDEO_ID.test(id) ? id : null;
  }

  function currentTitle() {
    const h1 = document.querySelector('h1.ytd-watch-metadata')?.textContent?.trim();
    if (h1) return h1;
    return document.title.replace(/^\(\d+\)\s*/, '').replace(/\s*-\s*YouTube\s*$/, '').trim();
  }

  function schedule() {
    clearTimeout(timer);
    const id = currentVideoId();
    if (!id || id === lastSent) return;
    timer = setTimeout(() => {
      if (currentVideoId() !== id) return;
      lastSent = id;
      chrome.runtime.sendMessage({ type: 'watch', videoId: id, title: currentTitle() }).catch(() => {
        // 확장 프로그램이 업데이트/재시작된 직후에는 연결이 끊길 수 있다
      });
    }, DWELL_MS);
  }

  // 유튜브는 SPA라 페이지 이동 시 이 이벤트가 발생한다
  document.addEventListener('yt-navigate-finish', schedule);
  window.addEventListener('popstate', schedule);
  schedule();
})();
