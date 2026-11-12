import test from "node:test";
import assert from "node:assert/strict";
import { ChatSession } from "../dist/chat.js";

function event(id, type, turnId, extra = {}) {
  return { id, event: type, data: JSON.stringify({ type, event_id: `${id}-logical`, turn_id: turnId, ...extra }) };
}
function turn(id, status = "completed", text = "第一行\n第二行") {
  return { id, status, yuxi: { result_run_id: `${id}-run`, output: status === "completed" ? [{ id: `${id}-item`, type: "message", role: "assistant", content: [{ type: "output_text", text }] }] : null } };
}

test("当前 Turn 的增量保留换行，持久输出确认完成且重复事件不会叠加", async () => {
  const cursors = [];
  let count = 0;
  const client = {
    async send() { return { input_id: `input-${++count}`, turn_id: null }; },
    async input() { return { status: "consumed", turn_id: `turn-${count}` }; },
    async turn(_session, id) { return turn(id); },
    async *events(_session, cursor) {
      cursors.push(cursor);
      const id = `turn-${count}`;
      yield event("old", "agent.session.turn.completed", "old-turn");
      const first = event(`${id}-1`, "agent.session.turn.output_text.delta", id, { item_id: `${id}-item`, delta: "第一行\n" });
      yield first;
      yield { ...first, id: "duplicate-cursor" };
      yield event(`${id}-settled`, "yuxi.session.run.settled", id);
      yield event(`${id}-2`, "agent.session.turn.output_text.delta", id, { item_id: `${id}-item`, delta: "第二行" });
      yield event(`${id}-done`, "agent.session.turn.completed", id);
    },
  };
  const session = new ChatSession(client, "thread");
  for (let index = 0; index < 2; index++) {
    const text = [];
    const result = await session.send("message", delta => text.push(delta));
    assert.deepEqual(text, ["第一行\n", "第二行"]);
    assert.equal(result.output, "第一行\n第二行");
    assert.equal(result.status, "completed");
  }
  assert.deepEqual(cursors, [undefined, "turn-1-done"]);
});

test("事件过期且没有任何正文增量，仍取得目标轮的持久结果", async () => {
  const client = {
    async send() { return { input_id: "input-target", turn_id: null }; },
    async input() { return { status: "consumed", turn_id: "target" }; },
    async turn(_session, id) { assert.equal(id, "target"); return turn(id, "completed", "持久最终结果"); },
    async *events() { yield event("resync", "yuxi.session.resync", "target"); },
  };
  const text = [];
  const result = await new ChatSession(client, "thread").send("message", delta => text.push(delta));
  assert.equal(result.output, "持久最终结果");
  assert.deepEqual(text, ["持久最终结果"]);
});

test("目标 Input 排队时按消费归属找 Turn，断流后补齐最终正文", async () => {
  const client = {
    async send() { return { input_id: "queued", turn_id: null }; },
    async input(_session, id) { assert.equal(id, "queued"); return { status: "consumed", turn_id: "own-turn" }; },
    async turn(_session, id) { return turn(id, "completed", "partial and final"); },
    async *events() { yield event("one", "agent.session.turn.output_text.delta", "own-turn", { item_id: "own-turn-item", delta: "partial" }); },
  };
  const text = [];
  const result = await new ChatSession(client, "thread").send("message", delta => text.push(delta));
  assert.equal(result.turnId, "own-turn");
  assert.equal(text.join(""), "partial and final");
});

test("相邻轮次已完成和事件断流都不能将目标轮结算", async () => {
  const client = {
    async send() { return { input_id: "input-target", turn_id: null }; },
    async input() { return { status: "consumed", turn_id: "target" }; },
    async turn(_session, id) { return turn(id, "running"); },
    async *events() { yield event("foreign", "agent.session.turn.completed", "neighbor"); },
  };
  await assert.rejects(() => new ChatSession(client, "thread").send("message", () => {}), /Turn target 尚未结束/);
});

test("协作等待继续观察，用户问答等待交还控制", async () => {
  let reads = 0;
  const client = {
    async send() { return { input_id: "input-target", turn_id: null }; },
    async input() { return { status: "consumed", turn_id: "target" }; },
    async turn(_session, id) { const result = turn(id, ++reads === 1 ? "in_progress" : "requires_action"); return result; },
    async *events() { yield event("wait-1", "yuxi.session.turn.waiting", "target"); yield event("wait-2", "yuxi.session.turn.waiting", "target"); },
  };
  assert.equal((await new ChatSession(client, "thread").send("message", () => {})).status, "requires_action");
  assert.equal(reads, 2);
});
