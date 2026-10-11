// 주제 목록 준비 단계 → 사용자에게 보여줄 문구와 진행률(0~1, 모르면 null)
export function describeIndexStatus(s = {}) {
  const pct = s.total ? s.done / s.total : null;
  const mb = (n) => Math.round(n / 1e6);
  switch (s.phase) {
    case 'listing':
      return { text: `위키백과에서 주제 목록을 모으는 중이에요 (${s.done ?? 0}개)`, progress: null };
    case 'translating':
      return { text: `한국어 문서를 찾는 중이에요 (${s.done}/${s.total})`, progress: pct };
    case 'describing':
      return { text: `문서 설명을 받는 중이에요 (${s.done}/${s.total})`, progress: pct };
    case 'downloading':
      return { text: `추천 모델을 내려받는 중이에요 — 처음 한 번만 (${mb(s.done)}/${mb(s.total)}MB)`, progress: pct };
    case 'embedding':
      return { text: `주제를 분석하는 중이에요 (${s.done}/${s.total})`, progress: pct };
    case 'ready':
      return { text: `주제 ${s.total}개 준비 완료`, progress: 1 };
    case 'error':
      return { text: `준비 중 문제가 생겼어요: ${s.error}. 몇 분 뒤 자동으로 다시 시도해요.`, progress: null };
    default:
      return { text: '추천할 주제 목록을 곧 준비할게요.', progress: null };
  }
}
