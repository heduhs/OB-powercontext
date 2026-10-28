// Public, open-source ONNX embedding models that run in the browser (no training, no cloud API).
export const MODELS = {
  "Xenova/bge-small-zh-v1.5": {
    label: "BGE-small-zh-v1.5",
    dimension: 512,
    pooling: "cls",
    maxSeq: 512,
    queryPrefix: "为这个句子生成表示以用于检索相关文章：",
    langs: "中文 + 英文",
    source: "BAAI/bge-small-zh-v1.5，Xenova 转换为 ONNX（Apache-2.0）",
  },
  "Xenova/all-MiniLM-L6-v2": {
    label: "all-MiniLM-L6-v2",
    dimension: 384,
    pooling: "mean",
    maxSeq: 512,
    queryPrefix: "",
    langs: "英文为主",
    source: "sentence-transformers/all-MiniLM-L6-v2，Xenova 转换为 ONNX（MIT）",
  },
};

export function pickModel(id) {
  return MODELS[id] || MODELS["Xenova/bge-small-zh-v1.5"];
}

export const DTYPE_FILE = {
  fp32: "onnx/model.onnx",
  fp16: "onnx/model_fp16.onnx",
  q8: "onnx/model_quantized.onnx",
};
