// 사전학습된 다국어 문장 임베딩 모델(multilingual-e5-small)을 브라우저 안에서 돌린다.
// 모델 파일(약 120MB)은 처음 한 번 Hugging Face 에서 받아 브라우저 캐시에 저장되고, 이후엔 오프라인으로 동작한다.
// 영상 제목·주제 텍스트는 외부로 나가지 않는다.

import { pipeline, env } from '../../vendor/transformers.js';

export const MODEL_ID = 'Xenova/multilingual-e5-small';
export const DIM = 384;

env.allowLocalModels = false;
// wasm 파일 위치만 알려준다. 접두사(문자열)를 주면 ORT 가 glue 스크립트를 import() 하려 드는데,
// 서비스 워커에서는 import() 가 금지라 실패한다. 객체로 wasm 만 주면 번들에 들어 있는 glue 를 쓴다.
env.backends.onnx.wasm.wasmPaths = { wasm: chrome.runtime.getURL('vendor/ort-wasm-simd-threaded.asyncify.wasm') };
env.backends.onnx.wasm.numThreads = 1; // 확장 프로그램에는 SharedArrayBuffer 가 없다

let extractorPromise = null;

/** @param onProgress 모델 다운로드 진행률 콜백 ({loaded, total}) */
export function loadModel(onProgress = () => {}) {
  if (!extractorPromise) {
    const files = new Map();
    extractorPromise = pipeline('feature-extraction', MODEL_ID, {
      dtype: 'q8',
      device: 'wasm',
      progress_callback: (p) => {
        if (p.status !== 'progress' || !p.total) return;
        files.set(p.file, { loaded: p.loaded, total: p.total });
        let loaded = 0;
        let total = 0;
        for (const f of files.values()) {
          loaded += f.loaded;
          total += f.total;
        }
        onProgress({ loaded, total });
      },
    }).catch((err) => {
      extractorPromise = null;
      throw err;
    });
  }
  return extractorPromise;
}

/**
 * 텍스트 → 정규화된 벡터. e5 계열은 입력 앞에 "query: " 를 붙이도록 학습됐다.
 * @returns {Promise<Float32Array[]>}
 */
export async function embedTexts(texts, onProgress) {
  const extractor = await loadModel(onProgress);
  const output = await extractor(
    texts.map((t) => `query: ${t}`),
    { pooling: 'mean', normalize: true },
  );
  const data = output.data;
  return texts.map((_, i) => data.slice(i * DIM, (i + 1) * DIM));
}
