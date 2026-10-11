// 실제 Chromium 에 확장을 올리고, Hugging Face / 위키백과 요청만 가짜 응답으로 바꿔서 전체 흐름을 돌린다.
// fakemodel/ 은 e5 와 입출력 모양만 같은 아주 작은 모델이다 (make_fake_model.py 로 생성).
// 실행: PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS=1 node tests/e2e/run.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const os = require('os');
// 실제 확장 폴더를 임시 폴더로 복사하고, 내장 데이터만 가짜로 바꿔 넣는다 (진짜 데이터 파일은 건드리지 않는다)
const ext = fs.mkdtempSync(path.join(os.tmpdir(), 'slightly-off-e2e-'));
fs.cpSync(path.resolve(__dirname, '../../extension'), ext, { recursive: true });
const OUT = process.env.E2E_OUT ?? __dirname;
const MODEL_DIR = path.join(__dirname, 'fakemodel');
const words = '재즈 피아노 연주 기타 코드 진행 밴드 라이브 공연 힙합 앨범 감상 클래식 오케스트라 드럼 영상 음악 음향 물리 요리 역사 수학 화학 건축'.split(' ');
const N = 300;
const ROOT = 'Wikipedia:Vital articles/Level/4';
const desc = (i) => `${words[i % words.length]} ${words[(i * 7) % words.length]} ${words[(i * 5 + 3) % words.length]}`;
const counts = { hf: 0, wiki: 0, other: [] };

function writeFakeData() {
  const DIM = 384;
  let seed = 1;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) * 2 - 1;
  const topics = Array.from({ length: N }, (_, i) => ({ id: `Topic ${i}`, title: `주제${i}`, description: desc(i) }));
  const scales = new Float32Array(N).fill(1 / 127);
  const data = new Int8Array(N * DIM).map(() => Math.round(rand() * 127));
  fs.mkdirSync(path.join(ext, 'data'), { recursive: true });
  fs.writeFileSync(path.join(ext, 'data/topics.json'), JSON.stringify({ meta: { version: 1, builtAt: new Date().toISOString(), count: N, dim: DIM, diagnostics: 'e2e' }, topics }));
  fs.writeFileSync(path.join(ext, 'data/topic-vectors.bin'), Buffer.concat([Buffer.from(scales.buffer), Buffer.from(data.buffer)]));
}

(async () => {
  writeFakeData();
  const ctx = await chromium.launchPersistentContext('', {
    channel: 'chromium', headless: true,
    args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`],
  });
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify(body) });

  await ctx.route(/huggingface\.co|hf\.co/, (route) => {
    counts.hf++;
    const m = new URL(route.request().url()).pathname.match(/^\/Xenova\/multilingual-e5-small\/resolve\/main\/(.+)$/);
    const file = m && path.join(MODEL_DIR, m[1]);
    if (file && fs.existsSync(file)) return route.fulfill({ status: 200, headers: { 'access-control-allow-origin': '*', 'content-length': String(fs.statSync(file).size) }, body: fs.readFileSync(file) });
    return route.fulfill({ status: 404, headers: { 'access-control-allow-origin': '*' }, body: 'not found' });
  });
  await ctx.route(/wikipedia\.org/, (route) => {
    counts.wiki++;
    const u = new URL(route.request().url());
    const p = u.searchParams;
    if (u.pathname.startsWith('/api/rest_v1/page/summary/')) {
      const t = decodeURIComponent(u.pathname.split('/').pop());
      return json(route, { extract: `${t}에 대한 요약입니다. (${desc(Number(t.replace(/\D/g, '')))})`, content_urls: { desktop: { page: `https://ko.wikipedia.org/wiki/${t}` } } });
    }
    const titles = (p.get('titles') ?? '').split('|');
    if (p.get('list') === 'categorymembers') return json(route, { query: { categorymembers: [] } });
    if (p.get('prop') === 'links') {
      const links = titles[0] === `${ROOT}/Arts` ? Array.from({ length: N }, (_, i) => ({ ns: 0, title: `Topic ${i}` })) : [];
      return json(route, { batchcomplete: true, query: { pages: [{ ns: 4, title: titles[0], links }] } });
    }
    if (p.get('prop') === 'langlinks') return json(route, { query: { pages: titles.map((t) => ({ ns: 0, title: t, langlinks: [{ lang: 'ko', title: `주제${t.split(' ')[1]}` }] })) } });
    if (p.get('prop') === 'description') return json(route, { query: { pages: titles.map((t) => ({ ns: 0, title: t, description: desc(Number(t.replace(/\D/g, ''))) })) } });
    counts.other.push(u.href);
    return json(route, { error: { code: 'unknown' } }, 400);
  });

  const isOurs = (w) => w.url().endsWith('/src/background.js');
  let sw = ctx.serviceWorkers().find(isOurs);
  while (!sw) { const w = await ctx.waitForEvent('serviceworker'); if (isOurs(w)) sw = w; }
  const id = new URL(sw.url()).host;
  const errors = [];
  ctx.on('weberror', (e) => errors.push(String(e.error())));

  const page = await ctx.newPage();
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  await page.setViewportSize({ width: 360, height: 600 });
  await page.goto(`chrome-extension://${id}/popup/popup.html`);
  const now = Date.now();
  const titles = ['재즈 피아노 연주', '기타 코드 진행', '밴드 라이브 공연', '힙합 앨범 감상', '클래식 오케스트라', '드럼 연주 영상', '요리 역사'];
  await page.evaluate(async ({ titles, now }) => {
    const videos = {};
    titles.forEach((t, i) => { const vid = ('vid' + i).padEnd(11, 'x'); videos[vid] = { id: vid, title: t, description: '', tags: [], categoryId: null, firstSeen: now, lastSeen: now - i * 3600e3, count: 1, enriched: true }; });
    await chrome.storage.local.set({ videos });
  }, { titles, now });

  await page.reload();
  const t0 = Date.now();
  let shotPreparing = false;
  for (;;) {
    const state = await page.evaluate(() => chrome.runtime.sendMessage({ type: 'getToday' }));
    const d = state.data;
    if (!shotPreparing && d?.status === 'preparing') { await page.reload(); await page.waitForTimeout(300); await page.screenshot({ path: path.join(OUT, 'preparing.png') }); shotPreparing = true; }
    if (d?.status === 'ready' || d?.indexStatus?.phase === 'error' || Date.now() - t0 > 90000) { console.log('final status', d?.status, JSON.stringify(d?.indexStatus), state.error ?? ''); break; }
    await page.waitForTimeout(1000);
  }
  console.log(`index built in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  await page.reload();
  await page.waitForSelector('.card', { timeout: 15000 });
  console.log('popup:', (await page.textContent('.card')).replace(/\s+/g, ' ').trim().slice(0, 300));
  console.log('links:', await page.getAttribute('.wiki', 'href'), await page.getAttribute('.youtube', 'href'));
  await page.screenshot({ path: path.join(OUT, 'popup.png') });
  await page.click('button[data-reaction="like"]');
  await page.waitForSelector('.thanks:not([hidden])');
  await page.screenshot({ path: path.join(OUT, 'popup-reacted.png') });

  const opt = await ctx.newPage();
  await opt.setViewportSize({ width: 800, height: 1600 });
  await opt.goto(`chrome-extension://${id}/options/options.html`);
  await opt.waitForSelector('#clusters li', { timeout: 15000 });
  await opt.click('#saved-summary');
  console.log('saved:', await opt.textContent('#saved-summary'), '|', (await opt.textContent('#saved-list li')).trim());
  await opt.screenshot({ path: path.join(OUT, 'options.png'), fullPage: true });
  console.log('options:', await opt.textContent('#index-status'), '|', await opt.textContent('#distance'));
  console.log('requests: hf', counts.hf, 'wiki', counts.wiki, 'unexpected', counts.other);
  console.log('errors:', errors);
  await ctx.close();
  if (errors.length || counts.other.length) process.exit(1);
})().catch((e) => { console.error('FAILED', e); process.exit(1); });
