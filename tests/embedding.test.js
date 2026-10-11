import { test } from 'node:test';
import assert from 'node:assert/strict';
import { embedVideo, embedTopic, distance, topDomains, rawVectorFromText, DOMAIN_INDEX } from '../extension/src/lib/embedding.js';
import { TOPICS } from '../extension/src/data/topics.js';
import { DOMAINS } from '../extension/src/data/domains.js';

test('텍스트 없는 영상은 임베딩하지 않는다', () => {
  assert.equal(embedVideo({ title: '' }), null);
  assert.equal(embedVideo({ title: 'asdf qwer' }), null);
});

test('제목 키워드로 도메인을 찾는다', () => {
  assert.equal(topDomains(embedVideo({ title: '재즈 피아노 연주' }), 1)[0][0], 'music');
  assert.equal(topDomains(embedVideo({ title: 'Minecraft speedrun world record' }), 1)[0][0], 'games');
});

test('영문 키워드는 단어 경계로만 매칭한다', () => {
  const v = rawVectorFromText({ title: 'she said that' });
  assert.equal(v[DOMAIN_INDEX.tech], 0); // "ai" in "said"
});

test('카테고리 ID 만으로도 신호가 생긴다', () => {
  assert.equal(topDomains(embedVideo({ title: '', categoryId: '17' }), 1)[0][0], 'sports');
});

test('이웃 분야는 무관한 분야보다 가깝다', () => {
  const music = embedVideo({ title: '피아노 연주' });
  const physics = embedVideo({ title: '양자 물리' });
  const fashion = embedVideo({ title: '메이크업 룩북' });
  assert.ok(distance(music, physics) < distance(music, fashion));
});

test('거리는 0~1 범위이고 자기 자신과는 0', () => {
  const v = embedVideo({ title: '축구 하이라이트 손흥민 골' });
  assert.ok(Math.abs(distance(v, v)) < 1e-9);
  for (const t of TOPICS) {
    const d = distance(v, embedTopic(t));
    assert.ok(d >= 0 && d <= 1, `${t.id}: ${d}`);
  }
});

test('주제 카탈로그는 유효한 도메인과 연결 문장을 갖는다', () => {
  const ids = new Set(DOMAINS.map((d) => d.id));
  const topicIds = new Set();
  for (const t of TOPICS) {
    assert.ok(!topicIds.has(t.id), `중복 id ${t.id}`);
    topicIds.add(t.id);
    assert.ok(t.title && t.summary, t.id);
    for (const d of Object.keys(t.domains)) assert.ok(ids.has(d), `${t.id}: ${d}`);
    for (const d of Object.keys(t.bridges)) assert.ok(ids.has(d), `${t.id} bridge: ${d}`);
    assert.ok(Object.keys(t.bridges).length >= 2, `${t.id}: 연결 문장이 부족함`);
  }
});
