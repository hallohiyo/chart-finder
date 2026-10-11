// 품질 확인용 보고서: 가상의 시청 기록 몇 가지로 실제 추천이 어떻게 나오는지 적는다.
// 목표 순위(targetRank)를 정할 때 이 표를 보고 판단한다.

import { scoreTopics, recommend, DEFAULT_PREFS } from '../extension/src/lib/recommender.js';
import { prepareIndex, toTopicSpace } from '../extension/src/lib/topic-space.js';

export const SAMPLE_PROFILES = {
  '음악': ['아이유 라이브 클립 모음', '재즈 피아노 플레이리스트 카페에서 듣기 좋은', '기타 코드 진행 쉽게 배우기', '밴드 합주 커버 영상', '힙합 앨범 리뷰 올해의 명반', '오케스트라 클래식 명곡 모음'],
  '게임': ['젤다의 전설 보스전 공략', '마인크래프트 야생 생존 1화', '닌텐도 다이렉트 신작 정리', '롤 챌린저 매드무비', '인디게임 추천 TOP 10', '스타크래프트 프로게이머 명경기'],
  '요리': ['백종원 김치찌개 레시피', '스테이크 굽는 법 셰프가 알려주는', '집에서 빵 만들기 초보 베이킹', '편의점 신상 먹방 리뷰', '자취 요리 일주일 식단', '파스타 소스 만들기'],
  '경제': ['주식 투자 기초 강의', '금리 인상이 부동산에 미치는 영향', '비트코인 전망 분석', '환율 오르면 생기는 일', '워런 버핏 투자 원칙', '인플레이션 쉽게 설명'],
  '코딩': ['파이썬 기초 강의 1강', '자바스크립트 비동기 완벽 정리', '개발자 브이로그 판교', '알고리즘 문제 풀이 백준', '리눅스 명령어 모음', '깃허브 사용법'],
  // 실제 시청 기록처럼 이것저것 섞인 경우: 막연한 주제가 끼어드는지 보는 용도
  '넓게 섞임 (실제 기록 흉내)': [
    '아이유 라이브 클립 모음', '마인크래프트 야생 생존 1화', '백종원 김치찌개 레시피', '손흥민 골 하이라이트', '로마 제국은 왜 멸망했나',
    '아이폰 16 프로 리뷰', '고양이 귀여운 순간 모음', '주식 투자 기초 강의', '침착맨 삼국지 이야기', '롤 챔피언스 코리아 하이라이트',
    '편의점 신상 먹방', '아이브 뮤직비디오', '2차 세계대전 다큐멘터리', '홈트레이닝 10분 루틴', '자동차 리뷰 신형 그랜저',
    '원피스 애니 명장면', '파이썬 기초 강의 1강', '여행 브이로그 일본 오사카', '공포 게임 실황', '영화 리뷰 인터스텔라 해석',
  ],
  '섞임 (축구+역사)': ['손흥민 골 하이라이트', '프리미어리그 이번 주 리뷰', '월드컵 명장면', '조선왕조 이야기 세종대왕', '로마 제국은 왜 멸망했나', '2차 세계대전 다큐멘터리'],
};

const TARGETS = [0.008, 0.015, 0.03, 0.06];

export async function buildReport({ topics, vectors, dim, embedTexts, meta }) {
  // 확장 프로그램과 똑같이 중심화한 공간에서 본다 (vectors 를 바꾸지 않도록 복사)
  const index = prepareIndex({ meta: { ...meta, dim }, topics, vectors: Float32Array.from(vectors) });
  const now = Date.now();
  const lines = [
    '# 내장 주제 데이터 보고서',
    '',
    `- 만든 시각: ${meta.builtAt}`,
    `- 주제 수: ${meta.count}개 (${meta.diagnostics})`,
    `- 모델: ${meta.model}`,
    '',
    '가상의 시청 기록으로 뽑아 본 추천입니다 (확장 프로그램과 같은 방식: 중심화 + 주제마다 가장 가까운 영상 5개의 평균). "가장 가까운 주제"는 너무 가까워서',
    `피해야 하는 쪽, 각 목표 순위 행은 그 설정에서 점수가 높은 후보 5개입니다. 기본값은 상위 ${DEFAULT_PREFS.targetRank * 100}%.`,
    '',
  ];
  for (const [name, titles] of Object.entries(SAMPLE_PROFILES)) {
    const videos = titles.map((title, i) => ({ id: `v${i}`, title, lastSeen: now - i * 3600e3, count: 1 }));
    const vecs = await embedTexts(titles);
    const pool = videos.map((v, i) => ({ ...v, vec: toTopicSpace(vecs[i], index), weight: 1 }));

    const nearest = scoreTopics({ videos: pool, index, prefs: { targetRank: 0.003 }, dateKey: 'x' })
      .sort((a, b) => a.rank - b.rank)
      .slice(0, 8)
      .map((c) => `${c.topic.title} (${c.similarity.toFixed(3)})`);
    const pick = recommend({ videos: pool, index, dateKey: '2026-01-01' });

    lines.push(`## ${name}`, '', `시청 예: ${titles.slice(0, 3).join(' / ')}`, '', `가장 가까운 주제: ${nearest.join(', ')}`, '');
    lines.push(
      `기본 설정의 추천: **${pick.title}** (상위 ${(pick.rank * 100).toFixed(1)}%) ← 가까운 영상: ${pick.nearVideos.map((v) => `${v.title} (${v.similarity})`).join(' / ')}`,
      '',
    );
    lines.push(`| 목표 순위 | 후보 (실제 순위 %) |`, `|---|---|`);
    for (const target of TARGETS) {
      const picks = scoreTopics({ videos: pool, index, prefs: { targetRank: target }, dateKey: '2026-01-01' })
        .slice(0, 5)
        .map((c) => `${c.topic.title} (${(c.rank * 100).toFixed(1)})`);
      lines.push(`| 상위 ${target * 100}% | ${picks.join(', ')} |`);
    }
    lines.push('');
  }
  return lines.join('\n');
}
