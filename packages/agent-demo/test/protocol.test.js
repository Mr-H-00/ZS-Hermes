import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyEvent,
  createItems,
  mergeSnapshot,
  readEvents,
} from "../src/protocol.js";

const message = (overrides = {}) => ({
  id: "message-1",
  type: "message",
  role: "assistant",
  status: "in_progress",
  content: [{ type: "output_text", text: "" }],
  yuxi: { run_id: "run-1", message_id: 2 },
  ...overrides,
});
const delta = (id, text) => ({
  type: "agent.session.turn.output_text.delta",
  event_id: id,
  item_id: "message-1",
  content_index: 0,
  delta: text,
});

test("重放事件不重复追加，完成正文拒绝迟到增量", () => {
  const state = createItems();
  mergeSnapshot(state, [message()]);
  applyEvent(state, delta("delta-1", "你好"));
  applyEvent(state, delta("delta-1", "你好"));
  assert.equal(state.items["message-1"].content[0].text, "你好");
  applyEvent(state, {
    type: "agent.session.turn.output_text.done",
    event_id: "done",
    item_id: "message-1",
    content_index: 0,
    text: "你好，世界",
  });
  applyEvent(state, delta("late", "迟到"));
  assert.equal(state.items["message-1"].content[0].text, "你好，世界");
});

test("resync 用已提交完成块补回 done，并保留其他块的新增量", () => {
  const state = createItems();
  mergeSnapshot(state, [
    message({
      content: [
        { type: "output_text", text: "丢失完成" },
        { type: "output_text", text: "较新内容" },
      ],
    }),
  ]);
  mergeSnapshot(state, [
    message({
      content: [
        { type: "output_text", text: "持久完成" },
        { type: "output_text", text: "较旧内容" },
      ],
      yuxi: { completed_content_indices: [0] },
    }),
  ]);
  applyEvent(state, delta("late", "错误增量"));
  assert.deepEqual(
    state.items["message-1"].content.map((part) => part.text),
    ["持久完成", "较新内容"],
  );
});

test("终态与 final_answer 不能被过期过程快照降级", () => {
  const state = createItems();
  mergeSnapshot(state, [
    message({
      status: "completed",
      phase: "final_answer",
      content: [{ type: "output_text", text: "最终结果" }],
    }),
  ]);
  mergeSnapshot(state, [message({ phase: "commentary" })]);
  applyEvent(state, delta("late", "污染"));
  assert.equal(state.items["message-1"].content[0].text, "最终结果");
  assert.equal(state.items["message-1"].phase, "final_answer");
});

test("推理完成块拒绝迟到增量", () => {
  const state = createItems();
  mergeSnapshot(state, [
    message({
      yuxi: { reasoning: { 0: "完成推理" }, completed_reasoning_indices: [0] },
    }),
  ]);
  applyEvent(state, {
    type: "yuxi.session.turn.reasoning.delta",
    event_id: "late",
    item_id: "message-1",
    content_index: 0,
    delta: "污染",
  });
  assert.equal(state.items["message-1"].yuxi.reasoning[0], "完成推理");
});

test("SSE 逐字节 UTF-8 与 CRLF 分块保持事件和 cursor，等待回调", async () => {
  const bytes = new TextEncoder().encode(
    ': heartbeat\r\n\r\nid: cursor-2\r\nevent: sample\r\ndata: {"event_id":"逻辑ID",\r\ndata: "delta":"你好"}\r\n\r\n',
  );
  const stream = new ReadableStream({
    start(controller) {
      for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
      controller.close();
    },
  });
  const events = [];
  await readEvents(
    new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
    async (event, cursor) => {
      await Promise.resolve();
      events.push({ event, cursor });
    },
  );
  assert.deepEqual(events, [
    { event: { event_id: "逻辑ID", delta: "你好" }, cursor: "cursor-2" },
  ]);
});

test("HTML 或格式错误不能冒充有效 SSE", async () => {
  await assert.rejects(
    () => readEvents(new Response("<html>"), () => {}),
    /有效的 SSE/,
  );
  await assert.rejects(
    () =>
      readEvents(
        new Response("data: broken\n\n", {
          headers: { "Content-Type": "text/event-stream" },
        }),
        () => {},
      ),
    SyntaxError,
  );
});
