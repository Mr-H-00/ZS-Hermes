export const STORAGE_KEY = "yuxi-agent-demo:v1";

/** 在 localhost 和局域网 HTTP 中生成随机 UUID。 */
export function newId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4),
    hex.slice(4, 6),
    hex.slice(6, 8),
    hex.slice(8, 10),
    hex.slice(10),
  ]
    .map((part) => part.join(""))
    .join("-");
}

/** 创建仅保存在浏览器中的 APP 配置。 */
export function newApp(index = 1) {
  return {
    id: newId(),
    name: `演示 APP ${index}`,
    appId: "",
    key: "",
    userId: "",
    users: [{ id: "", name: "默认用户" }],
  };
}

/** 读取本地配置，损坏的数据显式返回错误。 */
export function loadConfig(storage) {
  const initial = {
    baseUrl: "http://localhost:5173",
    layout: "phone",
    apps: [newApp()],
  };
  initial.activeAppId = initial.apps[0].id;
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return { config: initial, error: "" };
    const config = JSON.parse(raw);
    if (
      !["phone", "tablet"].includes(config.layout) ||
      typeof config.baseUrl !== "string" ||
      !Array.isArray(config.apps) ||
      !config.apps.length ||
      !config.apps.some((app) => app.id === config.activeAppId) ||
      config.apps.some(
        (app) =>
          !["id", "name", "appId", "key", "userId"].every(
            (key) => typeof app[key] === "string",
          ) ||
          !Array.isArray(app.users) ||
          !app.users.some((user) => user.id === app.userId) ||
          app.users.some(
            (user) =>
              typeof user.id !== "string" || typeof user.name !== "string",
          ),
      )
    ) {
      throw new Error("本地配置格式不正确");
    }
    return { config, error: "" };
  } catch {
    return {
      config: initial,
      error: "本地配置无法读取，已打开新的配置。保存连接后会替换损坏的配置。",
    };
  }
}

/** 校验用户输入的服务地址，只允许 HTTP 服务。 */
export function normalizeBaseUrl(value) {
  const url = new URL(value.trim());
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error("服务地址需要是无账号、查询参数的 HTTP 或 HTTPS 地址");
  }
  return url.href.replace(/\/+$/, "");
}
