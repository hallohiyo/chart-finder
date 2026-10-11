// 주제 벡터를 int8 로 압축해서 확장 프로그램에 내장한다 (벡터마다 스케일 하나).
// 파일 형식: [Float32 스케일 × N][Int8 값 × N·dim]  — 스케일을 앞에 둬서 4바이트 정렬을 지킨다.

export function quantize(vectors, dim) {
  const n = vectors.length / dim;
  const scales = new Float32Array(n);
  const data = new Int8Array(n * dim);
  for (let i = 0; i < n; i++) {
    let max = 0;
    for (let j = 0; j < dim; j++) max = Math.max(max, Math.abs(vectors[i * dim + j]));
    const scale = max / 127 || 1;
    scales[i] = scale;
    for (let j = 0; j < dim; j++) data[i * dim + j] = Math.round(vectors[i * dim + j] / scale);
  }
  return { scales, data };
}

/** quantize 결과 → 파일에 쓸 바이트 */
export function encodeVectors({ scales, data }) {
  const out = new Uint8Array(scales.byteLength + data.byteLength);
  out.set(new Uint8Array(scales.buffer, scales.byteOffset, scales.byteLength), 0);
  out.set(new Uint8Array(data.buffer, data.byteOffset, data.byteLength), scales.byteLength);
  return out;
}

/** 파일 바이트 → 정규화된 Float32 벡터 (길이 N·dim) */
export function decodeVectors(buffer, count, dim) {
  const bytes = buffer instanceof ArrayBuffer ? buffer : buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
  if (bytes.byteLength !== count * 4 + count * dim) {
    throw new Error(`주제 벡터 파일 크기가 맞지 않아요 (${bytes.byteLength}바이트, 주제 ${count}개)`);
  }
  const scales = new Float32Array(bytes, 0, count);
  const data = new Int8Array(bytes, count * 4, count * dim);
  const out = new Float32Array(count * dim);
  for (let i = 0; i < count; i++) {
    let norm = 0;
    for (let j = 0; j < dim; j++) {
      const v = data[i * dim + j] * scales[i];
      out[i * dim + j] = v;
      norm += v * v;
    }
    norm = Math.sqrt(norm) || 1;
    for (let j = 0; j < dim; j++) out[i * dim + j] /= norm;
  }
  return out;
}
