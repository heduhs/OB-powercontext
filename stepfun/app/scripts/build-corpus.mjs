/**
 * Build the demo corpus from open-source project documents (Apache-2.0 / MIT) plus
 * a few authored notes about on-device inference. Run once; the output under
 * data/corpus/ is committed so the app works without the original repos.
 *
 *   node scripts/build-corpus.mjs --repo seekdb=<path> --repo powercontext=<path>
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, "..");
const outDir = path.join(appRoot, "data", "corpus");

const CORPUS_SOURCES = [
  { repo: "seekdb", rel: "README_CN.md", label: "seekdb README（中文）", lang: "zh", topic: "seekdb", min: 160, max: 900 },
  { repo: "seekdb", rel: "README.md", label: "seekdb README（英文）", lang: "en", topic: "seekdb", min: 160, max: 900 },
  { repo: "seekdb", rel: "docs/blog/launch_blog_zh.md", label: "seekdb 发布博客（中文）", lang: "zh", topic: "seekdb", min: 200, max: 900 },
  { repo: "seekdb", rel: "docs/blog/launch_blog_en.md", label: "seekdb 发布博客（英文）", lang: "en", topic: "seekdb", min: 200, max: 900 },
  { repo: "powercontext", rel: "README_CN.md", label: "PowerContext README（中文）", lang: "zh", topic: "powercontext", min: 160, max: 900 },
];

const NOTE_TITLE = "端侧推理笔记";

function splitSections(md) {
  const lines = md.split(/\r?\n/);
  const out = [];
  let cur = { title: "", body: [] };
  let inCode = false;
  for (const line of lines) {
    if (/^```/.test(line)) {
      inCode = !inCode;
      continue;
    }
    if (inCode) continue;
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      if (cur.body.join(" ").trim().length > 60) out.push(cur);
      cur = { title: h[2].replace(/[^\p{Script=Han}A-Za-z0-9 ·\-—_]/gu, "").trim(), body: [] };
      continue;
    }
    if (/^\s*([-*•]|\d+\.)/.test(line)) cur.body.push(line.replace(/^\s*[-*•]\s*/, "· ").trim());
    else if (line.trim()) cur.body.push(line.trim());
  }
  if (cur.body.join(" ").trim().length > 60) out.push(cur);
  return out;
}

const clean = (t) =>
  t
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/\|/g, " ")
    .replace(/`{1,3}/g, "")
    .replace(/\s*\n\s*/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();

function pick(srcRoot, rel) {
  const abs = path.join(srcRoot, rel);
  if (!fs.existsSync(abs)) return null;
  return { abs, text: fs.readFileSync(abs, "utf8") };
}

const docs = [];
const roots = new Map();
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i] === "--repo") {
    const [name, ...rest] = process.argv[++i].split("=");
    roots.set(name, rest.join("="));
  }
}
if (!roots.size) {
  console.error("usage: node scripts/build-corpus.mjs --repo <name>=<path> [--repo <name>=<path> ...]");
  process.exit(1);
}

for (const cfg of CORPUS_SOURCES) {
  const root = roots.get(cfg.repo);
  if (!root) continue;
  const found = pick(root, cfg.rel);
  if (!found) {
    console.warn(`skip missing ${cfg.repo}/${cfg.rel}`);
    continue;
  }
  for (const sec of splitSections(found.text)) {
    const body = clean(sec.body.join(" "));
    if (body.length < cfg.min || body.length > cfg.max) continue;
    if (/(license|contributor|acknowledg|贡献者|免责声明)/i.test(sec.title)) continue;
    const id = `${cfg.topic}-${cfg.lang}-${String(docs.filter((d) => d.topic === cfg.topic && d.lang === cfg.lang).length + 1).padStart(2, "0")}`;
    docs.push({
      id,
      title: sec.title ? `${sec.title}（${cfg.label}）` : cfg.label,
      lang: cfg.lang,
      topic: cfg.topic,
      source: `${cfg.label} · ${cfg.rel}`,
      text: `${sec.title ? sec.title + "。" : ""}${body}`,
    });
  }
}

const notes = JSON.parse(fs.readFileSync(path.join(appRoot, "data", "edge-notes.json"), "utf8"));
for (const n of notes.documents) {
  docs.push({
    id: n.id,
    title: n.title,
    lang: n.id.includes("-zh-") ? "zh" : "en",
    topic: "edgeai",
    source: "作者撰写的端侧推理笔记",
    text: n.text,
  });
}

fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(outDir, { recursive: true });
for (const d of docs) fs.writeFileSync(path.join(outDir, d.id + ".md"), d.text + "\n", "utf8");
fs.writeFileSync(
  path.join(outDir, "index.json"),
  JSON.stringify(
    {
      name: "seekdb 与端侧 AI 技术资料（示例语料）",
      built_at: new Date().toISOString(),
      licence_note:
        "语料节选自 OceanBase seekdb / PowerContext 开源仓库文档（Apache-2.0）以及本项目作者撰写的端侧推理笔记，仅用于检索演示。",
      documents: docs.map((d) => ({ id: d.id, title: d.title, lang: d.lang, topic: d.topic, source: d.source, file: d.id + ".md", chars: d.text.length })),
    },
    null,
    2
  ),
  "utf8"
);
console.log(`corpus: ${docs.length} documents, ${docs.reduce((a, d) => a + d.text.length, 0)} chars -> data/corpus/`);
for (const d of docs) console.log(`  ${d.id}  [${d.lang}] ${d.title.slice(0, 40)}  ${d.text.length}`);
