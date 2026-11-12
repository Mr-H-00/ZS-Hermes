import test from "node:test";
import assert from "node:assert/strict";
import { ChatSession } from "../dist/chat.js";

function frame(id, type, turnId, delta, eventId = id) {
  return { id, event: type, data: JSON.stringify({ type, event_id: eventId, turn_id: turnId, item_id: "final", ...(delta === undefined ? {} : { delta }) }) };
}

test("reconnects from the last cursor after a clean stream interruption", async () => {
  let attempts = 0;
  const client = {
    async send() { return { input_id: "input", turn_id: null }; },
    async input() { return { status: "consumed", turn_id: "turn" }; },
    async turn() { return { id: "turn", status: attempts < 2 ? "in_progress" : "completed", yuxi: { output: attempts < 2 ? null : [{ id: "final", type: "message", role: "assistant", content: [{ type: "output_text", text: "第一行\n第二行" }] }] } }; },
    async *events(_threadId, cursor) {
      attempts += 1;
      if (!cursor) {
        yield frame("cursor-1", "agent.session.turn.output_text.delta", "turn", "第一行", "delta-1");
        return;
      }
      yield frame("cursor-2", "agent.session.turn.output_text.delta", "turn", "\n第二行", "delta-2");
      yield frame("cursor-3", "agent.session.turn.completed", "turn");
    },
  };
  let output = "";
  const result = await new ChatSession(client, "thread").send("message", text => { output += text; });
  assert.equal(output, "第一行\n第二行");
  assert.equal(result.status, "completed");
  assert.equal(attempts, 2);
});
