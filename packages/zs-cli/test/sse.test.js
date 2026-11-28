import test from "node:test";
import assert from "node:assert/strict";
import { parseSse } from "../dist/sse.js";

test("parses SSE fields and multiline data", async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(": heartbeat\nid: 7\nevent: message\ndata: {\n"));
      controller.enqueue(new TextEncoder().encode('data: \"ok\"}\n\n'));
      controller.close();
    },
  });
  assert.deepEqual(await Array.fromAsync(parseSse(body)), [{ id: "7", event: "message", data: '{\n"ok"}' }]);
});

test("断流时丢弃尚未结束的事件帧", async () => {
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('id: incomplete\ndata: {"type":"agent.session.turn.completed"}\n'));
    controller.close();
  } });
  assert.deepEqual(await Array.fromAsync(parseSse(body)), []);
});
