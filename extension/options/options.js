import { describeIndexStatus, describeModelCheck } from '../popup/status.js';
import { send as sendRaw, ensureFreshBackground } from '../popup/messaging.js';

const send = (type, payload) => sendRaw(type, payload, { reopen: 'options' });

const $ = (id) => document.getElementById(id);


function render(overview) {
  const { settings } = overview;
  $('paused').checked = settings.paused;
  $('max-per-day').value = String(settings.maxPerDay);
  $('api-key').value = '';
  $('api-key').placeholder = settings.apiKey ? `저장됨 (${settings.apiKey})` : 'AIza…';
  $('api-key-status').textContent = settings.apiKey
    ? overview.pendingCount
      ? ` 메타데이터를 기다리는 영상 ${overview.pendingCount}개`
      : ' 모든 영상의 메타데이터를 받아왔어요.'
    : '';

  $('saved-summary').textContent = `저장된 영상 보기 (${overview.videoCount}개)`;
  $('saved-list').replaceChildren(
    ...overview.recentVideos.map((v) => {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = `https://www.youtube.com/watch?v=${encodeURIComponent(v.id)}`;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = v.title;
      const meta = document.createElement('span');
      meta.className = 'muted';
      meta.textContent = ` · ${new Date(v.lastSeen).toLocaleDateString('ko-KR')}${v.count > 1 ? ` · ${v.count}번` : ''}`;
      li.append(a, meta);
      return li;
    }),
  );

  $('topic-status').textContent = overview.topicCount
    ? `내장된 주제 ${overview.topicCount}개 (${new Date(overview.topicsBuiltAt).toLocaleDateString('ko-KR')} 위키백과 기준)`
    : overview.indexError ?? '내장된 주제 데이터를 읽지 못했어요.';
  const { text, progress } = describeIndexStatus(overview.indexStatus);
  $('index-status').textContent = overview.modelReady ? '분석 모델 준비 완료' : text;
  const check = describeModelCheck(overview.indexStatus?.check);
  $('model-check').textContent = check?.text ?? '';
  $('model-check').className = check && !check.ok ? 'warn' : 'muted';
  const bar = $('index-progress');
  bar.hidden = overview.modelReady || progress == null;
  bar.querySelector('.progress-bar').style.setProperty('--p', `${Math.round((progress ?? 0) * 100)}%`);
  if (!overview.modelReady && overview.indexStatus?.phase !== 'error' && overview.indexStatus?.phase !== 'ready') pollSoon();

  $('map-summary').textContent = overview.clusters.length
    ? `저장된 영상 ${overview.videoCount}개를 비슷한 것끼리 묶었어요.`
    : overview.modelReady
      ? `아직 분석할 영상이 없어요 (저장된 영상 ${overview.videoCount}개).`
      : `분석 모델이 준비되면 보여드릴게요 (저장된 영상 ${overview.videoCount}개). 팝업을 한 번 열면 준비가 시작돼요.`;

  $('clusters').replaceChildren(
    ...overview.clusters.map((c) => {
      const li = document.createElement('li');
      const head = document.createElement('div');
      head.className = 'cluster-head';
      const name = document.createElement('strong');
      name.textContent = c.labels.join(' · ');
      const share = document.createElement('span');
      share.className = 'muted';
      const tight = c.cohesion >= 0.5 ? '높음' : c.cohesion >= 0.3 ? '보통' : '낮음';
      share.textContent = `영상 ${c.size}개 · 비중 ${c.weight}% · 비슷한 정도 ${tight}(${c.cohesion})`;
      head.append(name, share);
      const barEl = document.createElement('div');
      barEl.className = 'bar';
      barEl.style.setProperty('--w', `${c.weight}%`);
      const ex = document.createElement('ul');
      ex.className = 'examples muted';
      for (const title of c.examples) {
        const item = document.createElement('li');
        item.textContent = title;
        ex.append(item);
      }
      li.append(head, barEl, ex);
      return li;
    }),
  );

  $('recent-recs').replaceChildren(
    ...(overview.recentRecs.length
      ? overview.recentRecs.map((r) => {
          const li = document.createElement('li');
          const head = document.createElement('div');
          const title = document.createElement('strong');
          title.textContent = r.title;
          const meta = document.createElement('span');
          meta.className = 'muted';
          const reaction = r.reaction === 'like' ? ' · 😄' : r.reaction === 'dislike' ? ' · 🤔' : '';
          meta.textContent = ` · ${r.date}${r.rank != null ? ` · 상위 ${(r.rank * 100).toFixed(1)}%` : ''}${reaction}`;
          head.append(title, meta);
          const near = document.createElement('small');
          near.className = 'muted';
          near.textContent = r.nearVideos.length
            ? `가장 가까웠던 내 영상: ${r.nearVideos.map((v) => `${v.title} (${v.similarity})`).join(' / ')}`
            : '(이전 버전에서 만든 추천이라 기록이 없어요)';
          li.append(head, near);
          return li;
        })
      : [Object.assign(document.createElement('li'), { textContent: '아직 추천이 없어요.', className: 'muted' })]),
  );

  const pct = Math.round(overview.targetRank * 1000) / 10;
  $('distance').textContent =
    `현재 추천 거리: 관심사와 가까운 상위 ${pct}% 근처 주제 (재밌어요 → 더 멀리, 별로예요 → 더 가까이) · ` +
    `반응 — 재밌어요 ${overview.reactions.like}번, 별로예요 ${overview.reactions.dislike}번`;
}

let pollTimer = null;
function pollSoon() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(refresh, 3000);
}

async function refresh() {
  render(await send('overview'));
}

$('save').addEventListener('click', async () => {
  const settings = {
    paused: $('paused').checked,
    maxPerDay: Number($('max-per-day').value),
  };
  const key = $('api-key').value.trim();
  if (key) settings.apiKey = key;
  $('save-status').textContent = '저장 중…';
  try {
    render(await send('saveSettings', { settings }));
    $('save-status').textContent = '저장했어요.';
  } catch (err) {
    $('save-status').textContent = `저장 실패: ${err.message}`;
  }
});

$('import').addEventListener('click', async () => {
  const status = $('import-status');
  // 권한 요청은 사용자 클릭 직후에 해야 한다
  const granted = await chrome.permissions.request({ permissions: ['history'] });
  if (!granted) {
    status.textContent = '권한이 허용되지 않아 가져오지 않았어요.';
    return;
  }
  status.textContent = '가져오는 중…';
  try {
    const { imported } = await send('importHistory');
    status.textContent = `유튜브 영상 ${imported}개를 가져왔어요.`;
  } catch (err) {
    status.textContent = `가져오기 실패: ${err.message}`;
  } finally {
    await chrome.permissions.remove({ permissions: ['history'] });
    refresh();
  }
});

$('reset-today').addEventListener('click', async () => {
  $('reset-status').textContent = '새로 뽑는 중…';
  try {
    render(await send('resetToday'));
    $('reset-status').textContent = '새 추천을 뽑았어요. 툴바 아이콘을 눌러 확인하세요.';
  } catch (err) {
    $('reset-status').textContent = `실패: ${err.message}`;
  }
});

$('clear').addEventListener('click', async () => {
  if (!confirm('저장된 시청 기록과 추천 기록을 모두 지울까요? 되돌릴 수 없어요.')) return;
  render(await send('clearData'));
});

ensureFreshBackground('options')
  .then(({ page, background }) => {
    $('version').textContent = `버전: 화면 ${page} · 백그라운드 ${background ?? '확인 중'}`;
    return refresh();
  })
  .catch((err) => {
    $('version').textContent = err.message;
    $('version').className = 'warn';
  });
