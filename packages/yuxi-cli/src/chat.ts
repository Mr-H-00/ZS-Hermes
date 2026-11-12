import { randomUUID } from "node:crypto";
import type { Client } from "./client.js";
import type { Json } from "./types.js";
import { YuxiError } from "./types.js";

export type ChatTurnStatus = "completed" | "failed" | "cancelled" | "requires_action";
export interface ChatTurnResult { turnId: string; status: ChatTurnStatus; output: string; }
export type ChatDeltaHandler = (text: string) => void;
type ChatClient = Pick<Client, "send" | "events" | "input" | "turn">;

export class ChatSessionError extends Error {
  constructor(message: string) { super(message); this.name = "ChatSessionError"; }
}

/** 订阅只提供进度；接收归属和最终输出从目标 Input/Turn 回读。 */
export class ChatSession {
  private cursor: string | undefined;
  constructor(private readonly client: ChatClient, private readonly threadId: string) {}

  async send(message: string, onDelta: ChatDeltaHandler): Promise<ChatTurnResult> {
    const key = randomUUID();
    let accepted: Json;
    try { accepted = await this.client.send(this.threadId, message, key); }
    catch (error) { throw new ChatSessionError(`接收结果待确认；Session ${this.threadId}，Idempotency-Key ${key}：${error instanceof Error ? error.message : error}`); }
    let turnId = "";
    while (!turnId) {
      if (!accepted.input_id) throw new ChatSessionError("服务端回执缺少 input_id");
      const input = await this.client.input(this.threadId, String(accepted.input_id));
      if (input.status === "cancelled") throw new ChatSessionError(`Input ${accepted.input_id} 已取消`);
      turnId = String(input.turn_id ?? "");
      if (!turnId) await sleep(1000);
    }
    const printed = new Map<string, string>();
    const seenEventIds = new Set<string>();
    let reconnects = 0;
    while (true) {
      let snapshot: Json | undefined;
      try {
        for await (const event of this.client.events(this.threadId, this.cursor)) {
          let data: Json;
          try { data = JSON.parse(event.data || "{}"); }
          catch { throw new ChatSessionError("Session SSE data 不是 JSON"); }
          if (data.session_id && data.session_id !== this.threadId) throw new ChatSessionError("SSE Session 归属不一致");
          const type = String(data.type ?? event.event ?? "");
          if (data.turn_id === turnId && type === "agent.session.turn.output_text.delta") {
            const eventId = String(data.event_id ?? "");
            if (!seenEventIds.has(eventId)) {
              seenEventIds.add(eventId);
              const itemId = String(data.item_id);
              const delta = String(data.delta ?? "");
              printed.set(itemId, (printed.get(itemId) || "") + delta);
              onDelta(delta);
            }
          }
          const reconcile = type === "yuxi.session.resync" || (data.turn_id === turnId &&
            ["agent.session.turn.completed", "agent.session.turn.failed", "agent.session.turn.cancelled", "yuxi.session.turn.waiting"].includes(type));
          if (reconcile) {
            snapshot = await this.client.turn(this.threadId, turnId);
            if (isSettled(snapshot)) {
              if (event.id) this.cursor = event.id;
              break;
            }
          }
          // 快照应用失败不能推进恢复位置。
          if (event.id) this.cursor = event.id;
        }
      } catch (error) {
        if (error instanceof ChatSessionError || (error instanceof YuxiError && error.status && error.status < 500 && error.status !== 429)) throw error;
      }
      snapshot = snapshot && isSettled(snapshot) ? snapshot : await this.client.turn(this.threadId, turnId);
      if (isSettled(snapshot)) {
        const details = snapshot.yuxi as Json;
        const output = (details.output || []) as Json[];
        for (const item of output) {
          if (item.type !== "message" || item.role !== "assistant") continue;
          const text = ((item.content || []) as Json[]).filter(part => part.type === "output_text").map(part => String(part.text)).join("");
          const previous = printed.get(String(item.id)) || "";
          if (text !== previous) onDelta(text.startsWith(previous) ? text.slice(previous.length) : `\n${text}`);
        }
        return { turnId, status: snapshot.status as ChatTurnStatus,
          output: output.filter(item => item.type === "message" && item.role === "assistant")
            .flatMap(item => (item.content || []) as Json[]).filter(part => part.type === "output_text").map(part => String(part.text)).join("") };
      }
      if (reconnects >= 3) throw new ChatSessionError(`Session ${this.threadId} 的 Turn ${turnId} 尚未结束，可继续查询此 Turn`);
      await sleep(250 * 2 ** reconnects++);
    }
  }
}

/** 公开状态直接决定何时交还控制。 */
function isSettled(snapshot: Json): boolean {
  return ["completed", "failed", "cancelled", "requires_action"].includes(String(snapshot.status));
}
function sleep(milliseconds: number): Promise<void> { return new Promise(resolve => setTimeout(resolve, milliseconds)); }
