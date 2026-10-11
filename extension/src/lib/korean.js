// 한국어 조사 처리 유틸.

/** 마지막 글자에 받침이 있는지. 한글이 아니면 null */
export function hasFinalConsonant(word) {
  const ch = [...String(word).trim()].pop();
  if (!ch) return null;
  const code = ch.charCodeAt(0);
  if (code < 0xac00 || code > 0xd7a3) return null;
  return (code - 0xac00) % 28 !== 0;
}

/** "음악" → "음악을", "게임" → "게임을", "예능·방송" → "예능·방송을", "미술" → "미술을", "심리" → "심리를" */
export function withObjectParticle(word) {
  const final = hasFinalConsonant(word);
  return `${word}${final === false ? '를' : '을'}`;
}
