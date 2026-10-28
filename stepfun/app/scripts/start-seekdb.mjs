/**
 * Start a local seekdb server (MySQL protocol, 127.0.0.1:2881) for the app.
 *
 *   node scripts/start-seekdb.mjs [--port 2881] [--data <dir>] [--memory 2G]
 *
 * The binary is located from $SEEKDB_BIN, otherwise the usual install paths.
 * Linux/macOS install:  curl -s https://get.seekdb.cn | bash   (or use the official MSI/pkg)
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, "..");

const arg = (name, def) => {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 ? process.argv[i + 1] : def;
};

const candidates = [
  process.env.SEEKDB_BIN,
  "C:/Program Files/seekdb/bin/seekdb.exe",
  "/usr/local/bin/seekdb",
  path.join(os.homedir(), ".seekdb/bin/seekdb"),
].filter(Boolean);

const bin = candidates.find((c) => fs.existsSync(c));
if (!bin) {
  console.error(
    "未找到 seekdb 可执行文件。请安装 seekdb 后用 --bin 或环境变量 SEEKDB_BIN 指定路径。\n" +
      "  安装：curl -s https://get.seekdb.cn | bash   （Windows 下载 seekdb-1.3.0-win64.msi）\n" +
      "或者改用嵌入式模式：set SEEKDB_MODE=embedded（需安装 @seekdb/js-bindings 原生包）"
  );
  process.exit(1);
}

const baseDir = path.resolve(arg("data", path.join(appRoot, ".data", "server")));
const port = arg("port", "2881");
const memory = arg("memory", "2G");
fs.mkdirSync(baseDir, { recursive: true });

const args = [
  "--nodaemon",
  `--base-dir=${baseDir}`,
  `--port=${port}`,
  "--parameter",
  `memory_limit=${memory}`,
  "--parameter",
  `cpu_count=${Math.min(os.cpus().length, 4)}`,
  "--log-level",
  "WARN",
];
console.log(`$ ${bin} ${args.join(" ")}`);
const child = spawn(bin, args, { stdio: "inherit" });
child.on("exit", (code) => process.exit(code ?? 0));
process.on("SIGINT", () => child.kill("SIGINT"));
