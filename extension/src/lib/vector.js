export function dot(a, b, offset = 0) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[offset + i];
  return s;
}

export function normalize(v) {
  let n = 0;
  for (const x of v) n += x * x;
  n = Math.sqrt(n);
  if (n === 0) return v;
  for (let i = 0; i < v.length; i++) v[i] /= n;
  return v;
}
