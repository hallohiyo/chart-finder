import { describeIndexStatus } from './status.js';
import { send, ensureFreshBackground } from './messaging.js';

const view = document.getElementById('view');
const counter = document.getElementById('counter');
const stats = document.getElementById('stats');
let pollTimer = null;

document.getElementById('open-options').addEventListener('click', () => chrome.runtime.openOptionsPage());


function render(state) {
  clearTimeout(pollTimer);
  stats.textContent = `저장된 영상 ${state.videoCount}개`;
  counter.textContent = state.recommendation ? `${state.index} / ${state.maxPerDay}` : '';

  switch (state.status) {
    case 'ready':
      return renderRec(state);
    case 'collecting': {
      const remaining = Math.max(0, state.minVideos - state.videoCount);
      return renderEmpty({
        title: '관심사를 알아가는 중이에요',
        body: `유튜브 영상을 ${remaining}개만 더 보시면 첫 추천을 드릴게요. 설정에서 지난 시청 기록을 가져오면 바로 시작할 수 있어요.`,
        showOptions: true,
      });
    }
    case 'preparing': {
      const { text, progress } = describeIndexStatus(state.indexStatus);
      renderEmpty({ title: '첫 추천을 준비하고 있어요', body: text, progress });
      // 준비가 끝나면 바로 추천이 뜨도록 팝업이 열려 있는 동안 다시 물어본다
      pollTimer = setTimeout(() => run(() => send('getToday')), 2000);
      return;
    }
    case 'exhausted':
      return renderEmpty({ title: '오늘은 추천할 주제를 못 찾았어요', body: '시청 기록이 더 쌓이면 다시 찾아볼게요.' });
    case 'error':
      return renderEmpty({ title: '문제가 생겼어요', body: state.error ?? '' });
    default:
      return renderEmpty({ title: '문제가 생겼어요', body: `알 수 없는 상태: ${state.status}` });
  }
}

function renderRec(state) {
  const rec = state.recommendation;
  const node = document.getElementById('tpl-rec').content.cloneNode(true);
  node.querySelector('.title').textContent = rec.title;
  node.querySelector('.summary').textContent = rec.summary || rec.description || '';
  node.querySelector('.wiki').href = rec.url;
  node.querySelector('.youtube').href = rec.youtubeUrl;

  const buttons = node.querySelectorAll('.react');
  const thanks = node.querySelector('.thanks');
  const next = node.querySelector('.next');

  if (rec.reaction) {
    buttons.forEach((b) => {
      b.disabled = true;
      b.classList.toggle('chosen', b.dataset.reaction === rec.reaction);
    });
    thanks.hidden = false;
    thanks.textContent = rec.reaction === 'like' ? '다음엔 조금 더 멀리 가볼게요.' : '다음엔 관심사에 조금 더 가까이 골라볼게요.';
    if (state.canGetMore) {
      next.hidden = false;
      next.addEventListener('click', () => run(() => send('next')));
    } else {
      thanks.textContent += ' 내일 또 만나요.';
    }
  } else {
    buttons.forEach((b) =>
      b.addEventListener('click', () => run(() => send('react', { topicId: rec.topicId, reaction: b.dataset.reaction }))),
    );
  }
  view.replaceChildren(node);
}

function renderEmpty({ title, body, showOptions = false, progress = null }) {
  const node = document.getElementById('tpl-empty').content.cloneNode(true);
  node.querySelector('.empty-title').textContent = title;
  node.querySelector('.empty-body').textContent = body;
  if (progress != null) {
    const bar = node.querySelector('.progress');
    bar.hidden = false;
    bar.style.setProperty('--p', `${Math.round(progress * 100)}%`);
  }
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
    renderEmpty({ title: '문제가 생겼어요', body: err.message });
  }
}

run(async () => {
  await ensureFreshBackground(null);
  return send('getToday');
});
