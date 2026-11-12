#!/usr/bin/env node

import { openAsBlob } from "node:fs";
import { basename } from "node:path";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { Command } from "commander";
import { ChatSession } from "./chat.js";
import { Client } from "./client.js";
import { getRemote, loadConfig, normalizeUrl, resolveVerificationUrl, saveConfig } from "./config.js";
import { YuxiError } from "./types.js";
import type { Config, Json, Remote } from "./types.js";

const program = new Command()
  .name("yuxi")
  .description("Yuxi command line client")
  .version("0.1.0");
const remote = program.command("remote").description("管理 Yuxi 远程服务");
const agent = program.command("agent").description("查看可用 Agent");
const thread = program.command("thread").description("管理 Agent Thread");
const file = program.command("file").description("准备消息的 draft 文件");
const kb = program.command("kb").description("查询知识库");

function printJson(value: unknown): void {
  console.log(JSON.stringify(value, null, 2));
}

function print(value: unknown, asJson = false): void {
  if (asJson) {
    printJson(value);
    return;
  }
  console.log(typeof value === "string" ? value : JSON.stringify(value, null, 2));
}

async function context(name?: string): Promise<{
  config: Config;
  remote: Remote;
  client: Client;
}> {
  const config = await loadConfig();
  const selected = getRemote(config, name);
  return { config, remote: selected, client: new Client(selected) };
}

async function withErrors(action: () => Promise<void>): Promise<void> {
  try {
    await action();
  } catch (error) {
    console.error(`错误: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}

function idempotencyKey(): string {
  return randomUUID();
}

function requireAuth(remote: Remote): void {
  if (!remote.apiKey) throw new Error(`remote 尚未登录: ${remote.name}`);
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`远程响应缺少 ${field}`);
  return value;
}

function openUrl(url: string): void {
  const command = process.platform === "darwin" ? "open" : process.platform === "win32" ? "cmd" : "xdg-open";
  const args = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  execFile(command, args, error => {
    if (error) console.error(`无法自动打开浏览器，请手动访问: ${url}`);
  });
}

function isHeartbeat(data?: string): boolean {
  if (!data) return false;
  try {
    return JSON.parse(data).type === "yuxi.session.heartbeat";
  } catch {
    return false;
  }
}

remote
  .command("add <name> <url>")
  .action(async (name: string, url: string) => withErrors(async () => {
    const config = await loadConfig();
    const normalizedUrl = normalizeUrl(url);
    const old = config.remotes[name];
    config.remotes[name] = {
      name,
      url: normalizedUrl,
      ...(old && old.url === normalizedUrl ? old : {}),
    };
    await saveConfig(config);
    console.log(`已保存 remote ${name}: ${normalizedUrl}`);
  }));

remote
  .command("use <name>")
  .action(async (name: string) => withErrors(async () => {
    const config = await loadConfig();
    getRemote(config, name);
    config.current = name;
    await saveConfig(config);
    console.log(`当前 remote: ${name}`);
  }));

remote.command("list").action(() => withErrors(async () => {
  const config = await loadConfig();
  for (const item of Object.values(config.remotes)) {
    console.log(`${item.name === config.current ? "*" : " "} ${item.name}\t${item.url}\t${item.apiKey ? "API Key" : "未登录"}`);
  }
}));

remote.command("ping [name]").action((name?: string) => withErrors(async () => {
  const { remote, client } = await context(name);
  const data = await client.health();
  console.log(`${remote.name}: ${String(data.status ?? "ok")} ${String(data.version ?? "")}`.trim());
}));

program
  .command("login")
  .option("-r, --remote <name>")
  .option("--api-key <key>")
  .option("--no-open")
  .action((options: { remote?: string; apiKey?: string; open: boolean }) => withErrors(async () => {
    const config = await loadConfig();
    const selected = getRemote(config, options.remote);
    const client = new Client(selected);
    const discovery = await client.discovery();
    requiredString(discovery.version, "version");

    if (options.apiKey) {
      if (!options.apiKey.startsWith("yxkey_")) {
        throw new Error("API Key 格式无效，应以 yxkey_ 开头");
      }
      const probe = new Client({ ...selected, apiKey: options.apiKey });
      await probe.agents();
      selected.apiKey = options.apiKey;
      selected.apiKeyId = undefined;
      await saveConfig(config);
      console.log(`已保存 ${selected.name} 的 API Key。`);
      return;
    }

    const session = await client.createLoginSession();
    const deviceCode = requiredString(session.device_code, "device_code");
    const userCode = requiredString(session.user_code, "user_code");
    const verificationUri = requiredString(session.verification_uri, "verification_uri");
    const expiresIn = Number(session.expires_in);
    const interval = Number(session.interval);
    if (!Number.isFinite(expiresIn) || expiresIn <= 0 || !Number.isFinite(interval) || interval <= 0) {
      throw new Error("远程响应中的登录期限无效");
    }
    const url = resolveVerificationUrl(selected.url, verificationUri);
    url.searchParams.set("user_code", userCode);
    console.log(`授权码: ${userCode}\n浏览器授权地址: ${url}`);
    if (options.open) openUrl(url.toString());

    const deadline = Date.now() + expiresIn * 1000;
    while (Date.now() < deadline) {
      try {
        const token = await client.exchangeLoginToken(deviceCode);
        const secret = requiredString(token.secret, "secret");
        if (!secret.startsWith("yxkey_")) throw new Error("远程响应中的 API Key 格式无效");
        const apiKey = token.api_key;
        const apiKeyId = apiKey && typeof apiKey === "object" && !Array.isArray(apiKey)
          ? requiredString((apiKey as Json).id, "api_key.id")
          : "";
        selected.apiKey = secret;
        selected.apiKeyId = apiKeyId;
        await saveConfig(config);
        console.log(`已完成 ${selected.name} 的浏览器登录。`);
        return;
      } catch (error) {
        const exception = error as YuxiError;
        if (exception.status !== 400 && exception.status !== 429 && (exception.status ?? 0) < 500) {
          throw error;
        }
        await new Promise(resolve => setTimeout(resolve, interval * 1000));
      }
    }
    throw new Error("浏览器授权超时");
  }));

program.command("whoami").option("-r, --remote <name>").action((options: { remote?: string }) => withErrors(async () => {
  const { client } = await context(options.remote);
  printJson(await client.me());
}));

program.command("status").option("-r, --remote <name>").action((options: { remote?: string }) => withErrors(async () => {
  const { remote, client } = await context(options.remote);
  const health = await client.health();
  let auth = "未登录";
  if (remote.apiKey) {
    try {
      const user = await client.me();
      auth = String(user.username ?? "");
    } catch {
      auth = "API Key 无效或受限";
    }
  }
  console.log(`Remote: ${remote.name}\nURL: ${remote.url}\nHealth: ${String(health.status ?? "ok")} ${String(health.version ?? "")}\nAuth: ${auth}`);
}));

program
  .command("logout")
  .option("-r, --remote <name>")
  .option("--local-only")
  .action((options: { remote?: string; localOnly?: boolean }) => withErrors(async () => {
    const config = await loadConfig();
    const selected = getRemote(config, options.remote);
    if (!options.localOnly && selected.apiKeyId) await new Client(selected).deleteApiKey(selected.apiKeyId);
    delete selected.apiKey;
    delete selected.apiKeyId;
    await saveConfig(config);
    console.log(`已退出 ${selected.name}。`);
  }));

agent.command("list").option("-r, --remote <name>").option("--json").action((options: { remote?: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  print(await client.agents(), options.json);
}));

agent.command("show <id>").option("-r, --remote <name>").option("--json").action((id: string, options: { remote?: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  print(await client.agent(id), options.json);
}));

thread.command("list").option("-r, --remote <name>").option("--agent <id>").option("--after <id>").option("--limit <count>", "每页数量", "20").option("--order <order>", "asc 或 desc", "desc").option("--json").action((options: { remote?: string; agent?: string; after?: string; limit: string; order: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  const params = new URLSearchParams({ limit: options.limit, order: options.order });
  if (options.agent) params.set("agent_id", options.agent);
  if (options.after) params.set("after", options.after);
  print(await client.threads(`?${params}`), options.json);
}));

thread.command("show <id>").option("-r, --remote <name>").option("--json").action((id: string, options: { remote?: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  print(await client.thread(id), options.json);
}));

thread.command("history <id>").option("-r, --remote <name>").option("--after <id>").option("--limit <count>", "每页数量", "20").option("--order <order>", "asc 或 desc", "desc").option("--json").action((id: string, options: { remote?: string; after?: string; limit: string; order: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  const params = new URLSearchParams({ limit: options.limit, order: options.order });
  if (options.after) params.set("after", options.after);
  print(await client.items(id, `?${params}`), options.json);
}));

thread.command("send <id> <message>").option("-r, --remote <name>").option("--idempotency-key <key>", "重试复用的原请求键").option("--file-id <ids...>", "随消息提交的 draft ID").option("--image-url <urls...>", "完整的 data:image URL").action((id: string, message: string, options: { remote?: string; idempotencyKey?: string; fileId?: string[]; imageUrl?: string[] }) => withErrors(async () => {
  const { client } = await context(options.remote);
  const key = options.idempotencyKey || idempotencyKey();
  process.stderr.write(`Idempotency-Key: ${key}\n`);
  printJson(await client.send(id, message, key, options.fileId, options.imageUrl));
}));

thread.command("receipt <id> <key>").option("-r, --remote <name>").action((id: string, key: string, options: { remote?: string }) => withErrors(async () => {
  const { client } = await context(options.remote);
  printJson(await client.receipt(id, key));
}));

thread.command("input <id> <inputId>").option("-r, --remote <name>").action((id: string, inputId: string, options: { remote?: string }) => withErrors(async () => {
  const { client } = await context(options.remote);
  printJson(await client.input(id, inputId));
}));

thread.command("turn <id> <turnId>").option("-r, --remote <name>").action((id: string, turnId: string, options: { remote?: string }) => withErrors(async () => {
  const { client } = await context(options.remote);
  printJson(await client.turn(id, turnId));
}));

thread.command("turns <id>").option("-r, --remote <name>").option("--after <id>").option("--limit <count>", "每页数量", "20").option("--order <order>", "asc 或 desc", "desc").action((id: string, options: { remote?: string; after?: string; limit: string; order: string }) => withErrors(async () => {
  const { client } = await context(options.remote);
  const params = new URLSearchParams({ limit: options.limit, order: options.order });
  if (options.after) params.set("after", options.after);
  printJson(await client.turns(id, `?${params}`));
}));

file.command("upload <path>").option("-r, --remote <name>").option("--mime-type <type>", "文件 MIME", "application/octet-stream").action((path: string, options: { remote?: string; mimeType: string }) => withErrors(async () => {
  const { client } = await context(options.remote);
  printJson(await client.uploadFile(await openAsBlob(path, { type: options.mimeType }), basename(path)));
}));

file.command("show <id>").option("-r, --remote <name>").action((id: string, options: { remote?: string }) => withErrors(async () => {
  const { client } = await context(options.remote);
  printJson(await client.file(id));
}));

file.command("delete <id>").option("-r, --remote <name>").action((id: string, options: { remote?: string }) => withErrors(async () => {
  const { client } = await context(options.remote);
  printJson(await client.deleteFile(id));
}));

thread.command("watch <id>").option("-r, --remote <name>").option("--cursor <id>").action((id: string, options: { remote?: string; cursor?: string }) => withErrors(async () => {
  const { client } = await context(options.remote);
  for await (const event of client.events(id, options.cursor)) {
    if (isHeartbeat(event.data)) continue;
    if (event.id) process.stderr.write(`[${event.id}] `);
    console.log(event.data ?? "");
  }
}));

kb.command("list").option("-r, --remote <name>").option("--json").action((options: { remote?: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  print(await client.kbList(), options.json);
}));

kb.command("files").requiredOption("--kb-id <id>").option("--query <query>").option("-r, --remote <name>").option("--json").action((options: { kbId: string; query?: string; remote?: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  print(await client.kbFiles(options.kbId, options.query), options.json);
}));

kb.command("query <query>").requiredOption("--kb-id <id>").option("-r, --remote <name>").option("--json").action((query: string, options: { kbId: string; remote?: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  print(await client.kbQuery(options.kbId, query), options.json);
}));

kb.command("open").requiredOption("--kb-id <id>").requiredOption("--file-id <id>").option("-r, --remote <name>").option("--json").action((options: { kbId: string; fileId: string; remote?: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  print(await client.kbOpen(options.kbId, options.fileId), options.json);
}));

kb.command("find").requiredOption("--kb-id <id>").requiredOption("--file-id <id>").requiredOption("--pattern <pattern>").option("-r, --remote <name>").option("--json").action((options: { kbId: string; fileId: string; pattern: string; remote?: string; json?: boolean }) => withErrors(async () => {
  const { client } = await context(options.remote);
  print(await client.kbFind(options.kbId, options.fileId, [options.pattern]), options.json);
}));

program
  .command("chat")
  .option("-r, --remote <name>")
  .option("-a, --agent <id>", "Agent slug", "default-chatbot")
  .action((options: { remote?: string; agent: string }) => withErrors(async () => {
    const { client, remote } = await context(options.remote);
    requireAuth(remote);
    const created = await client.createThread(options.agent, idempotencyKey());
    const threadId = String(created.id);
    if (!threadId) throw new Error("服务端未返回 Session id");
    console.log(`Thread: ${threadId}`);

    const session = new ChatSession(client, threadId);
    const rl = createInterface({ input, output });
    try {
      while (true) {
        let message: string;
        try {
          message = (await rl.question("> ")).trim();
        } catch {
          break;
        }
        if (!message) continue;
        if (["/exit", "/quit"].includes(message)) break;
        const result = await session.send(message, text => process.stdout.write(text));
        if (result.status === "requires_action") {
          console.log("\n当前 Turn 等待用户操作，请在 Yuxi 网页继续。");
          break;
        }
        if (result.status === "failed") throw new Error(`Turn ${result.turnId} 执行失败`);
        if (result.status === "cancelled") throw new Error(`Turn ${result.turnId} 已取消`);
        process.stdout.write("\n");
      }
    } finally {
      rl.close();
    }
  }));

void program.parseAsync().catch(error => {
  console.error(`错误: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
