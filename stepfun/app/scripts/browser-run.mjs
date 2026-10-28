/**
 * Drive the app with a real Chrome instance over CDP (no extension/desktop automation needed).
 * Used to produce the measured evidence in ../records (benchmarks + screenshots).
 *
 *   node scripts/browser-run.mjs --task probe
 *   node scripts/browser-run.mjs --task smoke            # 主流程：加载模型 → 写入 → 检索 → 截图
 *   node scripts/browser-run.mjs --task eval             # 全量评测（4 个配置 + 消融），自动写 records
 *   node scripts/browser-run.mjs --task degrade          # 强制 WASM，验证降级路径
 *
 * Options: --port 9333 --headed --base http://127.0.0.1:8630 --timeout 900
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, "..");
const recordsDir = path.resolve(appRoot, "..", "records");

const argv = process.argv.slice(2);
const arg = (name, def) => {
  const i = argv.indexOf("--" + name);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : (argv[i] !== undefined ? true : def);
};

const BASE = String(arg("base", "http://127.0.0.1:8630"));
const PORT = Number(arg("port", 9333));
const TASK = String(arg("task", "probe"));
const HEADED = arg("headed", false) === true;
const DEADLINE = Number(arg("timeout", 1200)) * 1000;

const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"]
  .find((p) => fs.existsSync(p));
if (!CHROME) {
  console.error("未找到 Chrome，请安装最新版 Chrome/Edge 后用 --chrome=<路径> 指定");
  process.exit(1);
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "edgekb-chrome-"));
const chromeArgs = [
  "--remote-debugging-port=" + PORT,
  "--user-data-dir=" + profile,
  "--no-first-run",
  "--no-default-browser-check",
  "--window-size=1500,980",
  "--enable-unsafe-webgpu",
  "--use-angle=d3d11",
  ...(HEADED ? [] : ["--headless=new"]),
  "about:blank",
];

console.log("launching chrome:", CHROME, chromeArgs.join(" "));
const child = spawn(CHROME, chromeArgs, { stdio: "ignore" });
process.on("exit", () => {
  try { child.kill(); } catch { /* gone */ }
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForTarget(timeoutMs = 40000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await res.json();
      const page = list.find((t) => t.type === "page");
      if (page?.webSocketDebuggerUrl) return page;
    } catch { /* not up yet */ }
    await sleep(500);
  }
  throw new Error("CDP 未就绪");
}

const target = await waitForTarget();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => {
  ws.onopen = res;
  ws.onerror = rej;
});

let msgId = 0;
const waiting = new Map();
const events = [];
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && waiting.has(m.id)) {
    const { resolve, reject } = waiting.get(m.id);
    waiting.delete(m.id);
    m.error ? reject(new Error(m.error.message)) : resolve(m.result);
  } else if (m.method) {
    events.push(m);
  }
};

const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = ++msgId;
    waiting.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });

async function evaluate(expression, { awaitPromise = true, timeoutMs = 60000 } = {}) {
  const res = await send("Runtime.evaluate", {
    expression,
    awaitPromise,
    returnByValue: true,
    timeout: timeoutMs,
  });
  if (res.exceptionDetails) throw new Error("page exception: " + JSON.stringify(res.exceptionDetails).slice(0, 400));
  return res.result?.value;
}

async function navigate(url) {
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url });
  await sleep(2500);
}

async function shot(name) {
  fs.mkdirSync(path.join(recordsDir, "screenshots"), { recursive: true });
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  const file = path.join(recordsDir, "screenshots", name);
  fs.writeFileSync(file, Buffer.from(data, "base64"));
  console.log("  screenshot ->", "records/screenshots/" + name);
}

const step = (m) => console.log(`  ${m}`);

async function waitFor(expr, desc, timeoutMs = 300000, interval = 5000) {
  const t0 = Date.now();
  let last = "";
  while (Date.now() - t0 < timeoutMs) {
    last = String(await evaluate(expr));
    if (last.startsWith("DONE")) return last.slice(5);
    await sleep(interval);
  }
  throw new Error(`超时等待：${desc} | 最近状态：${last.slice(0, 300)}`);
}

const logTail = () => {
  return `(() => {
    const g = (id) => document.getElementById(id);
    return JSON.stringify({
      log: (g('log') ? g('log').textContent : '').split('\\n').slice(-6).join(' | '),
      status: g('load-status') ? g('load-status').textContent : null,
      ingest: g('ingest-status') ? g('ingest-status').textContent : null,
      badge: g('badge-backend') ? g('badge-backend').textContent : null,
    });
  })()`;
};

try {
  if (TASK === "probe") {
    await navigate(BASE + "/");
    const out = await evaluate(`(async () => {
      const a = navigator.gpu ? await navigator.gpu.requestAdapter() : null;
      return JSON.stringify({
        ua: navigator.userAgent,
        webgpu: !!a,
        shaderF16: a ? a.features.has('shader-f16') : false,
        gpuInfo: a && a.info ? a.info : null,
        cores: navigator.hardwareConcurrency,
        crossOriginIsolated: self.crossOriginIsolated,
        sab: typeof SharedArrayBuffer === 'function',
      });
    })()`);
    console.log(out);
  } else if (TASK === "smoke" || TASK === "degrade") {
    const forceWasm = TASK === "degrade";
    await navigate(BASE + "/");
    await evaluate(`(() => { const s = document.getElementById('${forceWasm ? "device" : "dtype"}'); s.value = '${forceWasm ? "wasm" : "auto"}'; return 1; })()`);
    await evaluate(`document.getElementById('btn-load').click(); 1;`);
    const load = await waitFor(
      `(() => { const s = document.getElementById('load-status').textContent; return (s.indexOf('已就绪')>=0||s.indexOf('失败')>=0) ? 'DONE '+s+' / '+document.getElementById('badge-backend').textContent : 'WAIT '+s; })()`,
      "模型加载",
      180000,
      3000
    );
    step("模型: " + load);
    await evaluate(`document.getElementById('btn-sample').click(); 1;`);
    await sleep(1500);
    await evaluate(`document.getElementById('btn-ingest').click(); 1;`);
    const ing = await waitFor(
      `(() => { const t = document.getElementById('ingest-status').textContent; return (t.indexOf('已写入')>=0||t.indexOf('失败')>=0) ? 'DONE '+t+' || '+document.getElementById('ingest-metrics').innerText.replace(/\\n/g,' · ') : 'WAIT '+t; })()`,
      "写入",
      420000,
      5000
    );
    step("写入: " + ing);
    await evaluate(`(() => { document.getElementById('explain').checked = true; document.getElementById('query').value = 'seekdb 如何在一条 SQL 里完成向量与全文混合检索？'; document.getElementById('btn-search').click(); return 1; })()`);
    const q = await waitFor(
      `(() => { const r = document.getElementById('results').innerText; const m = document.getElementById('query-metrics').innerText; if (m.indexOf('失败')>=0) return 'DONE ERR ' + m; return r && r.length > 30 ? 'DONE ' + m.replace(/\\n/g,' · ') + ' || ' + r.slice(0,260) : 'WAIT empty'; })()`,
      "检索",
      180000,
      3000
    );
    step("检索: " + q);
    await shot(`${TASK}-app.png`);
    const evidence = await evaluate(`document.getElementById('evidence').textContent.slice(0, 1200)`);
    console.log("  seekdb SQL 证据:\n" + evidence);
    const payload = await evaluate(
      `JSON.stringify({task:'${TASK}',backend:document.getElementById('badge-backend').textContent,engine:document.getElementById('engine-info').innerText,ingest:document.getElementById('ingest-metrics').innerText,query:document.getElementById('query-metrics').innerText,evidence:document.getElementById('evidence').textContent,results:document.getElementById('results').innerText.slice(0,3000)})`
    );
    fs.mkdirSync(recordsDir, { recursive: true });
    fs.writeFileSync(path.join(recordsDir, `run-${TASK}.json`), JSON.stringify(JSON.parse(payload), null, 2));
    console.log(`  saved records/run-${TASK}.json`);
  } else if (TASK === "eval") {
    await navigate(BASE + "/eval.html");
    await sleep(1500);
    const only = String(arg("configs", "")).split(",").filter(Boolean);
    if (only.length) {
      await evaluate(
        `(() => { const keep = ${JSON.stringify(only)};
          document.querySelectorAll('input[data-key]').forEach(el => { el.checked = keep.includes(el.dataset.key); });
          return document.querySelectorAll('input[data-key]:checked').length; })()`
      );
      step("仅运行配置：" + only.join(","));
    }
    const recordName = arg("record", "");
    if (recordName) {
      await evaluate(`(() => { const el = document.getElementById('record-name'); el.value = ${JSON.stringify(String(recordName))}; return el.value; })()`);
      step("结果文件：" + recordName);
    }
    await evaluate(`document.getElementById('btn-run').click(); 1;`);
    step("评测已启动（4 配置 + 消融）");
    const done = await waitFor(
      `(() => { const l = document.getElementById('log').textContent; if (l.indexOf('原始数据已写入')>=0) return 'DONE ' + l.split('\\n').slice(-2).join(' | '); if (l.indexOf('评测失败')>=0) return 'DONE ' + l.slice(-500); return 'WAIT ' + l.split('\\n').slice(-2).join(' | '); })()`,
      "评测完成",
      DEADLINE,
      10000
    );
    step("评测: " + done);
    const tables = await evaluate(
      `JSON.stringify({sizes:document.getElementById('sizes').innerText,quality:document.getElementById('quality').innerText,perf:document.getElementById('perf').innerText,modes:document.getElementById('modes').innerText,ablation:document.getElementById('ablation').innerText,log:document.getElementById('log').textContent})`
    );
    fs.mkdirSync(path.join(recordsDir, "screenshots"), { recursive: true });
    fs.writeFileSync(path.join(recordsDir, "run-eval-tables.json"), JSON.stringify(JSON.parse(tables), null, 2));
    console.log("\n=== 质量 ===\n" + JSON.parse(tables).quality);
    console.log("\n=== 性能 ===\n" + JSON.parse(tables).perf);
    await shot("eval-report.png");
  } else {
    throw new Error("unknown --task " + TASK);
  }
} catch (e) {
  console.error("FAILED:", e.message);
  process.exitCode = 2;
} finally {
  ws.close();
  try { child.kill(); } catch { /* gone */ }
  setTimeout(() => process.exit(process.exitCode || 0), 1500).unref();
}
