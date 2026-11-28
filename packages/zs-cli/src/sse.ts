import type { SseEvent } from "./types.js";

export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event: SseEvent = {};
  let data: string[] = [];
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line === "") {
          if (data.length) yield { ...event, data: data.join("\n") };
          event = {}; data = []; continue;
        }
        if (line.startsWith(":")) continue;
        const [field, ...parts] = line.split(":");
        const value = parts.join(":").replace(/^ /, "");
        if (field === "data") data.push(value);
        else if (field === "event" || field === "id") event[field] = value;
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
