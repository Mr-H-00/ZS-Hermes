import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import type { Config, Remote } from "./types.js";

export const configPath = join(homedir(), ".yuxi", "config.json");
const defaultConfig = (): Config => ({ current: "local", remotes: { local: { name: "local", url: "http://localhost:5173" } } });

export function normalizeUrl(input: string): string {
  let value = input.trim();
  if (!value) throw new Error("remote URL 不能为空");
  if (!value.includes("://")) value = `http://${value}`;
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error("remote URL 仅支持 http 或 https");
  let path = url.pathname.replace(/\/+$/, "");
  if (path === "/api") path = "";
  else if (path.endsWith("/api")) path = path.slice(0, -4);
  return `${url.protocol}//${url.host}${path}`;
}

export async function loadConfig(path = configPath): Promise<Config> {
  try {
    const raw = JSON.parse(await readFile(path, "utf8")) as Partial<Config>;
    const remotes = Object.fromEntries(Object.entries(raw.remotes ?? {}).map(([name, value]) => [name, { name, url: normalizeUrl(String((value as Remote).url ?? "")), apiKey: (value as Remote).apiKey, apiKeyId: (value as Remote).apiKeyId }]));
    if (!Object.keys(remotes).length) return defaultConfig();
    const current = typeof raw.current === "string" && remotes[raw.current] ? raw.current : Object.keys(remotes)[0];
    return { current, remotes };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return defaultConfig();
    if (error instanceof SyntaxError) throw new Error(`配置文件 JSON 格式无效: ${path}`);
    throw error;
  }
}

export async function saveConfig(config: Config, path = configPath): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  await writeFile(path, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
  await chmod(path, 0o600);
}

export function getRemote(config: Config, name?: string): Remote {
  const remote = config.remotes[name ?? config.current];
  if (!remote) throw new Error(`remote 不存在: ${name ?? config.current}`);
  return remote;
}

export function resolveVerificationUrl(remoteUrl: string, verificationUri: string): URL {
  if (verificationUri.startsWith("http://") || verificationUri.startsWith("https://")) return new URL(verificationUri);
  return new URL(`${remoteUrl.replace(/\/$/, "")}/${verificationUri.replace(/^\//, "")}`);
}
