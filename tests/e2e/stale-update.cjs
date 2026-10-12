// "업데이트했는데 백그라운드가 옛 버전" 상황 재현:
// 옛 버전(ping·resetToday 없음)으로 로드 → ↻ 없이 디스크 파일만 새 버전으로 덮어쓰기 → 설정 페이지에서 다시 뽑기.
// 새 설정 페이지가 버전 차이를 알아채고 스스로 확장 프로그램을 다시 불러와야(chrome.runtime.reload) 한다.
//
// 한계: Playwright 처럼 --load-extension 으로 올린 확장은 chrome.runtime.reload() 후 되살아나지 않는다
// (아무 기능 없는 확장으로도 똑같다). 그래서 이 테스트는 "다시 불러오기를 시작했는지"까지만 확인한다.
// 실제 크롬("압축해제된 확장 프로그램을 로드합니다")에서는 ↻ 버튼과 같은 동작이다.
// 실행: PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS=1 node tests/e2e/stale-update.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const os = require('os');
const path = require('path');
const SRC = path.resolve(__dirname, '../../extension');
const ext = fs.mkdtempSync(path.join(os.tmpdir(), 'slightly-off-stale-'));
const MODEL_DIR = path.join(__dirname, 'fakemodel');
const N = 120;

function copyNew() {
  fs.cpSync(SRC, ext, { recursive: true });
  const DIM = 384;
  let seed = 1;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) * 2 - 1;
  const topics = Array.from({ length: N }, (_, i) => ({ id: `Topic ${i}`, title: `주제${i}`, description: '재즈 피아노' }));
  fs.mkdirSync(path.join(ext, 'data'), { recursive: true });
  fs.writeFileSync(path.join(ext, 'data/topics.json'), JSON.stringify({ meta: { version: 1, builtAt: new Date().toISOString(), count: N, dim: DIM, diagnostics: 'e2e' }, topics }));
  const scales = new Float32Array(N).fill(1 / 127);
  const data = new Int8Array(N * DIM).map(() => Math.round(rand() * 127));
  fs.writeFileSync(path.join(ext, 'data/topic-vectors.bin'), Buffer.concat([Buffer.from(scales.buffer), Buffer.from(data.buffer)]));
}

function makeOld() {
  // 옛 백그라운드: ping 과 resetToday 를 모른다
  const bg = path.join(ext, 'src/background.js');
  let s = fs.readFileSync(bg, 'utf8');
  s = s.replace(/    case 'ping':\n.*\n/, '').replace(/    case 'resetToday':\n.*\n.*\n/, '');
  if (s.includes("'ping'") || s.includes("'resetToday'")) throw new Error('old background patch failed');
  fs.writeFileSync(bg, s);
  fs.writeFileSync(path.join(ext, 'src/version.js'), "export const CODE_VERSION = '0.3.0';\n");
}

(async () => {
  copyNew();
  makeOld();
  const ctx = await chromium.launchPersistentContext('', { channel: 'chromium', headless: true, args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`] });
  await ctx.route(/huggingface\.co|hf\.co/, (route) => {
    const m = new URL(route.request().url()).pathname.match(/^\/Xenova\/multilingual-e5-small\/resolve\/main\/(.+)$/);
    const file = m && path.join(MODEL_DIR, m[1]);
    if (file && fs.existsSync(file)) return route.fulfill({ status: 200, headers: { 'access-control-allow-origin': '*' }, body: fs.readFileSync(file) });
    return route.fulfill({ status: 404, body: '' });
  });
  await ctx.route(/wikipedia\.org/, (route) => route.fulfill({ status: 404, body: '' }));
  const isOurs = (w) => w.url().endsWith('/src/background.js');
  let sw = ctx.serviceWorkers().find(isOurs);
  while (!sw) { const w = await ctx.waitForEvent('serviceworker'); if (isOurs(w)) sw = w; }
  const id = new URL(sw.url()).host;

  // 옛 버전 상태로 기록 몇 개 + 모델 준비
  const page = await ctx.newPage();
  await page.goto(`chrome-extension://${id}/popup/popup.html`);
  await page.evaluate(async () => {
    const now = Date.now();
    const videos = {};
    ['재즈 피아노 연주', '기타 코드 진행', '밴드 라이브 공연', '힙합 앨범 감상', '클래식 오케스트라', '드럼 연주 영상'].forEach((t, i) => {
      const vid = ('vid' + i).padEnd(11, 'x');
      videos[vid] = { id: vid, title: t, description: '', tags: [], categoryId: null, firstSeen: now, lastSeen: now, count: 1, enriched: true };
    });
    await chrome.storage.local.set({ videos });
  });
  const old = await page.evaluate(() => chrome.runtime.sendMessage({ type: 'resetToday' }));
  console.log('옛 백그라운드에 resetToday:', JSON.stringify(old));

  // ↻ 없이 디스크만 새 버전으로 (update.bat / ZIP 덮어쓰기와 같은 상황)
  copyNew();
  await page.close();

  const opt = await ctx.newPage();
  // 페이지가 확장 프로그램을 다시 불러오면서 스스로 닫히므로 이동이 끊기는 게 정상이다
  const nav = await opt.goto(`chrome-extension://${id}/options/options.html`).then(() => 'loaded', (e) => e.message.split('\n')[0]);
  await new Promise((r) => setTimeout(r, 3000));
  const reloaded = ctx.serviceWorkers().every((w) => !isOurs(w)) || nav.includes('ERR_BLOCKED_BY_CLIENT') || opt.isClosed();
  console.log('설정 페이지 열기:', nav);
  console.log('옛 백그라운드를 알아채고 다시 불러오기 시작:', reloaded);
  await ctx.close();
  if (!reloaded) process.exit(1);
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
