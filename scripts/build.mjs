// Transformers.js 와 ONNX Runtime(wasm)을 확장 프로그램 안에 묶는다.
// MV3 는 원격 코드 로딩을 금지하므로 런타임은 모두 extension/vendor/ 에 들어가야 한다.
// (모델 가중치는 코드가 아니라서 첫 실행 때 Hugging Face 에서 내려받아 캐시한다)
import { build } from 'esbuild';
import { copyFile, mkdir } from 'node:fs/promises';

const outdir = 'extension/vendor';
await mkdir(outdir, { recursive: true });

await build({
  stdin: { contents: "export { pipeline, env } from '@huggingface/transformers';", resolveDir: '.' },
  bundle: true,
  format: 'esm',
  platform: 'browser',
  target: 'chrome120',
  minify: true,
  outfile: `${outdir}/transformers.js`,
  logLevel: 'warning',
});

// 번들된 ORT 는 import.meta.url 기준으로 이 파일을 찾는다
await copyFile(
  'node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.asyncify.wasm',
  `${outdir}/ort-wasm-simd-threaded.asyncify.wasm`,
);
console.log('built extension/vendor');
