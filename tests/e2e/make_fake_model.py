# e2e 테스트용 가짜 임베딩 모델: 단어 → 고정 벡터(Gather) 한 층짜리 ONNX + WordLevel 토크나이저.
# multilingual-e5-small 과 입출력 이름/모양(last_hidden_state, 384차원)만 같다.
# pip install onnx numpy && python3 tests/e2e/make_fake_model.py
import json, os
import numpy as np, onnx
from onnx import helper, TensorProto, numpy_helper

out = os.path.join(os.path.dirname(__file__), 'fakemodel')
os.makedirs(os.path.join(out, 'onnx'), exist_ok=True)
words = "재즈 피아노 연주 기타 코드 진행 밴드 라이브 공연 힙합 앨범 감상 클래식 오케스트라 드럼 영상 음악 음향 물리 요리 역사 수학 화학 건축".split()
vocab = {"[PAD]": 0, "[UNK]": 1, **{w: i + 2 for i, w in enumerate(words)}}
E = np.random.default_rng(0).normal(size=(len(vocab), 384)).astype(np.float32)
E[0] = 0
g = helper.make_graph(
    [helper.make_node("Gather", ["E", "input_ids"], ["last_hidden_state"], axis=0)],
    "tiny",
    [helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b", "s"]),
     helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b", "s"])],
    [helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["b", "s", 384])],
    [numpy_helper.from_array(E, "E")])
m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
m.ir_version = 8
onnx.checker.check_model(m)
onnx.save(m, os.path.join(out, "onnx", "model_quantized.onnx"))
special = [{"id": i, "content": c, "single_word": False, "lstrip": False, "rstrip": False, "normalized": False, "special": True}
           for c, i in (("[PAD]", 0), ("[UNK]", 1))]
json.dump({"version": "1.0", "truncation": None, "padding": None, "added_tokens": special, "normalizer": None,
           "pre_tokenizer": {"type": "Whitespace"}, "post_processor": None, "decoder": None,
           "model": {"type": "WordLevel", "vocab": vocab, "unk_token": "[UNK]"}},
          open(os.path.join(out, "tokenizer.json"), "w"), ensure_ascii=False)
json.dump({"tokenizer_class": "PreTrainedTokenizerFast", "pad_token": "[PAD]", "unk_token": "[UNK]", "model_max_length": 512},
          open(os.path.join(out, "tokenizer_config.json"), "w"))
json.dump({"model_type": "bert", "hidden_size": 384, "architectures": ["BertModel"]}, open(os.path.join(out, "config.json"), "w"))
