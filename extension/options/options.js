const $ = (id) => document.getElementById(id);

async function send(type, payload = {}) {
  const res = await chrome.runtime.sendMessage({ type, ...payload });
  if (!res?.ok) throw new Error(res?.error ?? '알 수 없는 오류');
  return res.data;
}

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

  $('map-summary').textContent = overview.clusters.length
    ? `저장된 영상 ${overview.videoCount}개에서 찾은 관심 묶음이에요.`
    : `아직 분석할 영상이 없어요 (저장된 영상 ${overview.videoCount}개).`;

  $('clusters').replaceChildren(
    ...overview.clusters.map((c) => {
      const li = document.createElement('li');
      const head = document.createElement('div');
      head.className = 'cluster-head';
      const name = document.createElement('strong');
      name.textContent = c.domains.join(' · ');
      const share = document.createElement('span');
      share.className = 'muted';
      share.textContent = `${c.weight}% · 영상 ${c.size}개`;
      head.append(name, share);
      const bar = document.createElement('div');
      bar.className = 'bar';
      bar.style.setProperty('--w', `${c.weight}%`);
      const rep = document.createElement('small');
      rep.className = 'muted';
      rep.textContent = `대표 영상: ${c.representative || '(제목 없음)'}`;
      li.append(head, bar, rep);
      return li;
    }),
  );

  const pct = Math.round(overview.targetDistance * 100);
  $('distance').textContent =
    `현재 추천 거리: ${pct} / 100 (낮을수록 익숙한 주제, 높을수록 낯선 주제) · ` +
    `반응 — 재밌어요 ${overview.reactions.like}번, 별로예요 ${overview.reactions.dislike}번`;
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

$('clear').addEventListener('click', async () => {
  if (!confirm('저장된 시청 기록과 추천 기록을 모두 지울까요? 되돌릴 수 없어요.')) return;
  render(await send('clearData'));
});

refresh();
