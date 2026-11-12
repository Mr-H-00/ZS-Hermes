# 以真实模块测试收敛测试维护成本

状态：implemented
类型：testing
Owner：frontend/test/helpers/createTestServer.js

## 问题

部分前端测试按字符串位置抽取函数、删除 import 或改写编译结果后执行，源码排版和内部重构会导致测试返工，也可能绕开真实装配。系统测试的构建和执行成本较高，但减少启动次数不等于降低等待时间，也可能破坏隔离。

## 决策

目标是保留现有独立失败、并发、重试和状态断言，减少对业务源码写法的依赖。非目标是设置覆盖率或测试数量配额、重构业务实现、添加测试框架或合并有隔离职责的 pytest 进程。

### 实现方案

前端通过现有 Vite 测试服务器加载完整 Vue/JS/TS 模块；替身位于 API、路由、浏览器及不属于被测行为的依赖边界。需要客户端模板的 renderer 测试正常编译完整 SFC，不修改业务函数或生成代码。真实 Vue 响应式、生命周期与 Pinia 支撑组件逻辑断言；模板行为使用完整 SSR 输出或客户端 renderer。静态资源、样式归属和架构检查仍检查其静态事实，SSR 不替代浏览器 E2E。

会话和工作区测试分别复用领域装配 fixture，负责真实组件与 Store 的初始化、卸载及全局状态恢复；不引入通用测试框架。Vite helper 关闭测试不使用的热更新 WebSocket，避免并行服务器争用固定端口。测试维护规则由[测试规范](../../testing-guidelines.md)拥有。

系统测试保留具有 fixture 清理、进程或事件循环隔离职责的执行阶段；镜像准备拓扑保留。已有两个 job 并行构建，集中构建会增加等待及镜像传输，没有可比证据不改变拓扑。CI 的粗粒度选择、报告与稳定汇总见[门禁决定](2026-10-29-ci-required-gate-and-test-evidence.md)，目录职责见[脚本归位决定](2026-10-29-ci-script-ownership.md)。

## 替代方案

- 继续执行源码片段：准备简单，但重构脆弱且绕过真实导入，不采用。
- 新增测试框架或为测试拆分大量生产函数：扩大依赖或业务改动，现有 Vite 和 node:test 已满足需求，不采用。
- 直接合批 pytest 或共享构建：可能减少重复工作，也改变清理、连接池与调度边界；先测量收益和隔离正确性。

## 后果

真实模块加载增加部分测试准备成本，换取导入、响应式与生命周期证据。维护成本不以测试行数衡量；新增用例需说明独立失败面，删除需有替代证据。领域 fixture 只服务实际消费者，不能扩张为另一个运行器。

远端 required 检查与规则需要部署后的真实运行核验。本地单测证明选择器和失败传播，不能证明 GitHub 已阻止合并；最终推送和远端验证由汇总任务统一执行。

## 验证

- 前端迁移保留既有失败、取消、冲突、迟到响应、重复提交和权限状态断言；定向测试与 ESLint 通过。`pnpm --dir frontend run lint:check` 和 `pnpm --dir frontend run build` 通过；`pnpm --dir frontend run test:unit`：949 项通过、零失败、零跳过；902 个顶层测试保持，参数分支改用子测试后计数从 945 变为 949，并非新增四个业务场景。独立 Reviewer 发现的重复错误提示断言遗漏已补回，相关 31 项再次通过，最终审查无阻断项。
- 前端完整运行约 196 秒；迁移前样本约 93 秒。真实组件加载增加本地执行成本，样本不是受控性能实验，不作为提速证据；本决定接受该成本以保留真实导入与生命周期观察，不以替身跳过被测边界换取速度。
- 定向恢复 Widget 布局错误、记忆导航错误、主题回退错误，以及文件搜索、知识预览和文件图谱的过期响应缺陷，替代测试均按目标原因失败；恢复后通过。生产代码保持不变。
- CI 脚本 `backend/.venv/bin/python -m unittest discover -s scripts/ci -p 'test_*.py'`：98 项通过，含真实 Docker 的构建上下文与权限检查；系统 Python 无 pytest，真实 JUnit 样本使用项目 Python 环境执行。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：3357 passed、125 skipped；skip 不计作通过。工程契约检查及其 56 项自测通过。
- 文档 `pnpm --dir docs run build`、64 个修改文档相对链接、actionlint、CI Shell 语法、Python Ruff 与 `git diff --check` 通过。
- 只读 CI 基线：[system 成功运行 40818155239](https://github.com/xerrors/Yuxi/actions/runs/40818155239) 等待 37 分 30 秒，总 job 执行 45 分 13 秒，两个镜像构建步骤分别约 283/421 秒，运行恢复阶段约 544–548 秒。单次成功结果不证明 flake 率或优化收益；应比较环境、缓存相近的多次成功 PR 等待时间和总 runner 时间。
- Not run：最终提交在 GitHub 的条件调度、artifact 上传、必需失败/取消导致真实合并阻断，以及优化前后可比耗时。只读查询未见 main 的 required status-check 规则，不宣称门禁已生效。完整系统 E2E 和浏览器交互未因本测试装配变更重跑。
