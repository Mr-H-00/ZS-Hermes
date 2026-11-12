import assert from "node:assert/strict";
import { chromium } from "@playwright/test";

const key = process.env.DEMO_API_KEY;
if (!key)
  throw new Error("真实链路验证需要 DEMO_API_KEY（绑定 APP 的 agents Key）");
const baseUrl = process.env.DEMO_BASE_URL || "http://localhost:5173";
const demoUrl = process.env.DEMO_URL || "http://localhost:5180";
const secondKey = process.env.DEMO_SECOND_API_KEY;
const agentId = process.env.DEMO_AGENT_ID;
const expected = process.env.DEMO_EXPECTED_OUTPUT;
const injectFaults = process.env.DEMO_RECOVERY_FAULTS === "1";
let lostResponse = false, brokenStream = false, eventPosts = 0, receiptReads = 0;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const headers = { Authorization: `Bearer ${key}` };
let threadId;

/** 回读真实 Public API，错误只输出状态。 */
async function get(path) {
  const response = await fetch(`${baseUrl}/api/v1/agents${path}`, { headers });
  assert.equal(response.status, 200, `Public 回读失败：${response.status}`);
  return response.json();
}

/** 等待数据库投影出现明确的整轮结果。 */
async function awaitResults() {
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    const session = await get(`/sessions/${threadId}`);
    const page = await get(`/sessions/${threadId}/items?limit=100`);
    if (session.yuxi.current_turn?.status === "failed") {
      const turn = await get(
        `/sessions/${threadId}/turns/${session.yuxi.current_turn.id}`,
      );
      const failed = turn.yuxi.runs.find((run) => run.status === "failed");
      const detail = String(failed?.error_message || "")
        .replaceAll(key, "[hidden]")
        .slice(0, 300);
      throw new Error(
        `真实 Agent 执行失败：${failed?.error_type || "unknown"} ${detail}`,
      );
    }
    const final = page.data.filter((item) => item.phase === "final_answer");
    if (
      final.length >= 2 &&
      session.yuxi.current_turn?.status === "completed" &&
      !session.yuxi.queued_input_count
    )
      return page;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("120 秒内未观察到两轮持久最终结果");
}

try {
  await page.addInitScript(
    (settings) =>
      localStorage.setItem("yuxi-agent-demo:v1", JSON.stringify(settings)),
    {
      baseUrl,
      layout: "phone",
      activeAppId: "live",
      apps: [
        {
          id: "live",
          name: "真实链路验证",
          appId: "",
          key,
          userId: "",
          users: [
            { id: "", name: "默认用户" },
            { id: `demo-live-other-${crypto.randomUUID()}`, name: "隔离用户" },
          ],
        },
        ...(secondKey
          ? [
              {
                id: "other",
                name: "第二 APP",
                appId: "",
                key: secondKey,
                userId: "",
                users: [{ id: "", name: "默认用户" }],
              },
            ]
          : []),
      ],
    },
  );
  // 同源部署时可把页面资源转发到独立 Demo，API 仍访问真实部署。
  if (process.env.DEMO_ASSET_PROXY) {
    const origin = new URL(demoUrl).origin;
    await page.route(`${origin}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/")) return route.continue();
      const path = url.pathname === new URL(demoUrl).pathname ? "/" : url.pathname;
      const response = await route.fetch({url: `${process.env.DEMO_ASSET_PROXY}${path}${url.search}`});
      await route.fulfill({response});
    });
  }
  if (injectFaults) {
    await page.route("**/sessions/*/events", async (route) => {
      if (route.request().method() === "POST") {
        eventPosts++;
        if (!lostResponse) {
          const accepted = await route.fetch();
          assert.equal(accepted.status(), 202);
          await accepted.body();
          lostResponse = true;
          return route.abort("failed");
        }
      } else if (!brokenStream) {
        brokenStream = true;
        return route.abort("failed");
      }
      await route.continue();
    });
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/receipt")) receiptReads++;
    });
  }
  await page.goto(demoUrl);
  await page.getByLabel("消息输入").waitFor({ state: "visible" });
  await page.waitForFunction(
    () => !document.querySelector('[aria-label="消息输入"]').disabled,
  );
  if (agentId) {
    await page.getByLabel("选择 Agent").selectOption(agentId);
    await page.waitForFunction(() => !document.querySelector('[aria-label="消息输入"]').disabled);
  }
  await page.locator('input[type="file"]').first().setInputFiles({ name: "demo-live.txt", mimeType: "text/plain", buffer: Buffer.from("Demo attachment bytes") });
  await page.getByText("demo-live.txt", {exact:true}).waitFor();
  const prompt = `YUXI_TEST_SESSION_DEMO_${crypto.randomUUID().slice(0, 8)} 请只回复 DEMO_FIRST_OK，不调用工具。`;
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/agents/sessions",
  );
  await page.getByLabel("消息输入").fill(prompt);
  await page.getByRole("button", { name: "发送消息" }).click();
  threadId = (await (await created).json()).id;
  assert.ok(threadId);
  let attachment;
  const attachmentDeadline = Date.now() + 30000;
  while (Date.now() < attachmentDeadline) {
    const attachmentPage = await get(`/sessions/${threadId}/attachments`);
    attachment = attachmentPage.attachments.find((file) => file.file_name === "demo-live.txt");
    if (attachment?.input_id) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.ok(attachment?.input_id);
  const original = await fetch(new URL(attachment.original_artifact_url, baseUrl), {headers});
  assert.equal(original.status, 200);
  assert.equal(await original.text(), "Demo attachment bytes");
  await page.waitForFunction(
    () => !document.querySelector('[aria-label="消息输入"]').disabled,
  );
  await page
    .getByLabel("消息输入")
    .fill("请只回复 DEMO_SECOND_OK，不调用工具。");
  await page.getByRole("button", { name: "发送消息" }).click();
  if (injectFaults) {
    await page.getByRole("button", {name:"重试",exact:true}).waitFor();
    await page.getByRole("button", {name:"重试",exact:true}).click();
  }
  const history = await awaitResults();
  const finalItems = history.data.filter(
    (item) => item.phase === "final_answer",
  );
  assert.ok(
    finalItems.some((item) =>
      item.content.some((part) => part.text?.includes(expected || "DEMO_FIRST_OK")),
    ),
  );
  assert.ok(
    finalItems.some((item) =>
      item.content.some((part) => part.text?.includes(expected || "DEMO_SECOND_OK")),
    ),
  );
  for (const item of finalItems) {
    const turn = await get(`/sessions/${threadId}/turns/${item.turn_id}`);
    assert.equal(turn.status, "completed");
    assert.equal(turn.yuxi.result_run_id, item.yuxi.run_id);
  }
  await page.getByText(expected || "DEMO_SECOND_OK", { exact: true }).last().waitFor();
  await page.getByRole("tab", { name: "用户", exact: true }).click();
  await page.getByRole("button", { name: /隔离用户/ }).click();
  await page.waitForFunction(
    () => !document.querySelector('[aria-label="消息输入"]').disabled,
  );
  assert.ok(
    !(await page.getByLabel("对话消息").textContent()).includes(
      expected || "DEMO_FIRST_OK",
    ),
  );
  await page.getByRole("button", { name: /默认用户 默认身份/ }).click();
  await page.waitForFunction(
    () => !document.querySelector('[aria-label="消息输入"]').disabled,
  );
  if (agentId) {
    await page.getByLabel("选择 Agent").selectOption(agentId);
    await page.waitForFunction(() => !document.querySelector('[aria-label="消息输入"]').disabled);
  }
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page
    .getByRole("button", { name: new RegExp(prompt.slice(0, 36)) })
    .click();
  await page.getByText(expected || "DEMO_SECOND_OK", { exact: true }).last().waitFor();
  if (secondKey) {
    await page.getByRole("tab", { name: "连接", exact: true }).click();
    await page.getByRole("button", { name: /第二 APP 待识别/ }).click();
    await page.waitForFunction(
      () => !document.querySelector('[aria-label="消息输入"]').disabled,
    );
    assert.ok(
      !(await page.getByLabel("对话消息").textContent()).includes(
        expected || "DEMO_FIRST_OK",
      ),
    );
  }
  if (injectFaults) {
    assert.equal(eventPosts, 1, "回执恢复不应重复 POST");
    assert.ok(receiptReads > 0 && lostResponse && brokenStream);
  }
  await page.screenshot({path:"/tmp/yuxi-query-demo-final.png",fullPage:true});
  console.log(
    JSON.stringify({
      realApi: true,
      draftAttachment: true,
      ...(injectFaults ? {lostResponse, brokenStream, eventPosts, receiptReads} : {}),
      workerFinalTurns: finalItems.length,
      browserFinalOutput: true,
      userIsolation: true,
      appSwitch: Boolean(secondKey),
      threadId,
    }),
  );
} catch (error) {
  await page.screenshot({path:"/tmp/yuxi-query-demo-failure.png",fullPage:true});
  throw error;
} finally {
  await browser.close();
  if (threadId) {
    const snapshot = await get(`/sessions/${threadId}`);
    if (
      ["completed", "failed", "cancelled"].includes(
        snapshot.yuxi.current_turn?.status,
      )
    ) {
      await fetch(`${baseUrl}/api/v1/agents/sessions/${threadId}/archive`, {
        method: "POST",
        headers,
      });
    }
  }
}
