// 코드 버전. manifest.json 의 version 과 같게 유지한다 (tests/version.test.js 가 확인).
// 페이지는 이 파일을 디스크에서 새로 읽고, 옛 서비스 워커는 메모리의 옛 값을 갖고 있어서
// 둘을 비교하면 "업데이트했는데 백그라운드가 옛 버전" 상태를 알아낼 수 있다.
// (chrome.runtime.getManifest() 는 둘 다 같은 값을 돌려줘서 이 용도로는 못 쓴다)
export const CODE_VERSION = '0.3.1';
