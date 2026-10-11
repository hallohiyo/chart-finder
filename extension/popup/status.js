// 분석 모델 준비 단계 → 사용자에게 보여줄 문구와 진행률(0~1, 모르면 null)
export function describeIndexStatus(s = {}) {
  const pct = s.total ? s.done / s.total : null;
  const mb = (n) => Math.round(n / 1e6);
  switch (s.phase) {
    case 'downloading':
      return s.total
        ? { text: `분석 모델을 내려받는 중이에요 — 처음 한 번만 (${mb(s.done)}/${mb(s.total)}MB)`, progress: pct }
        : { text: '분석 모델을 준비하는 중이에요', progress: null };
    case 'ready':
      return { text: '분석 모델 준비 완료', progress: 1 };
    case 'error':
      return { text: `준비 중 문제가 생겼어요: ${s.error}. 몇 분 뒤 자동으로 다시 시도해요.`, progress: null };
    default:
      return { text: '분석 모델을 곧 내려받을게요.', progress: null };
  }
}

/** 자가 점검 결과 → 문구. check: 브라우저 모델과 내장 데이터의 일치도 (1 = 완전히 같음) */
export function describeModelCheck(check) {
  if (check == null) return null;
  const pct = (check * 100).toFixed(1);
  return check >= 0.98
    ? { ok: true, text: `모델 점검: 정상 (내장 데이터와 일치도 ${pct}%)` }
    : { ok: false, text: `모델 점검: 이상 — 내장 데이터와 일치도 ${pct}%. 이 상태에선 추천이 엉뚱할 수 있어요. 이 문구를 개발자에게 알려 주세요.` };
}
