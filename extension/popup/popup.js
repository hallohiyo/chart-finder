const view = document.getElementById('view');
const counter = document.getElementById('counter');
const stats = document.getElementById('stats');

document.getElementById('open-options').addEventListener('click', () => chrome.runtime.openOptionsPage());

async function send(type, payload = {}) {
  const res = await chrome.runtime.sendMessage({ type, ...payload });
  if (!res?.ok) throw new Error(res?.error ?? '알 수 없는 오류');
  return res.data;
}

function render(state) {
  stats.textContent = `분석한 영상 ${state.videoCount}개`;
  counter.textContent = state.recommendation ? `${state.index} / ${state.maxPerDay}` : '';

  if (state.status === 'ready' && state.recommendation) return renderRec(state);
  if (state.status === 'exhausted') {
    return renderEmpty('준비된 주제를 모두 보여드렸어요', '새로운 주제가 추가되면 다시 추천해 드릴게요.');
  }
  const remaining = Math.max(0, state.minVideos - state.videoCount);
  renderEmpty(
    '관심사를 알아가는 중이에요',
    `유튜브 영상을 ${remaining}개만 더 보시면 첫 추천을 드릴게요.` +
      (state.hasApiKey ? '' : ' 설정에서 YouTube API 키를 넣거나 지난 시청 기록을 가져오면 더 정확해져요.'),
    !state.hasApiKey,
  );
}

function renderRec(state) {
  const rec = state.recommendation;
  const node = document.getElementById('tpl-rec').content.cloneNode(true);
  node.querySelector('.title').textContent = rec.title;
  node.querySelector('.reason').textContent = rec.reason;
  node.querySelector('.anchor').textContent = rec.anchorTitle ? `연결고리: 최근 본 「${rec.anchorTitle}」` : '';
  node.querySelector('.summary').textContent = rec.summary;
  node.querySelector('.more').href = rec.link;

  const buttons = node.querySelectorAll('.react');
  const thanks = node.querySelector('.thanks');
  const next = node.querySelector('.next');

  if (rec.reaction) {
    buttons.forEach((b) => {
      b.disabled = true;
      b.classList.toggle('chosen', b.dataset.reaction === rec.reaction);
    });
    thanks.hidden = false;
    thanks.textContent =
      rec.reaction === 'like'
        ? '좋아요! 다음엔 조금 더 멀리 가볼게요.'
        : '알겠어요. 다음엔 관심사에 조금 더 가까운 주제로 골라볼게요.';
    if (state.canGetMore) {
      next.hidden = false;
      next.addEventListener('click', () => run(() => send('next')));
    } else {
      thanks.textContent += ' 다음 추천은 내일 만나요.';
    }
  } else {
    buttons.forEach((b) =>
      b.addEventListener('click', () => run(() => send('react', { topicId: rec.topicId, reaction: b.dataset.reaction }))),
    );
  }
  view.replaceChildren(node);
}

function renderEmpty(title, body, showOptions = false) {
  const node = document.getElementById('tpl-empty').content.cloneNode(true);
  node.querySelector('.empty-title').textContent = title;
  node.querySelector('.empty-body').textContent = body;
  const btn = node.querySelector('.to-options');
  if (showOptions) {
    btn.hidden = false;
    btn.addEventListener('click', () => chrome.runtime.openOptionsPage());
  }
  view.replaceChildren(node);
}

async function run(fn) {
  try {
    render(await fn());
  } catch (err) {
    renderEmpty('문제가 생겼어요', err.message);
  }
}

run(() => send('getToday'));
