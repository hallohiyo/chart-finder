import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { CODE_VERSION } from '../extension/src/version.js';

test('코드 버전과 manifest 버전이 같다 (업데이트 감지에 쓰인다)', () => {
  const manifest = JSON.parse(readFileSync(new URL('../extension/manifest.json', import.meta.url)));
  assert.equal(CODE_VERSION, manifest.version);
});
