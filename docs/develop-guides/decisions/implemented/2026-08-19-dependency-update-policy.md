# 依赖更新降噪策略

状态：implemented
类型：process
Owner：.github/dependabot.yml

## 问题

Dependabot 对七类依赖源每周独立创建常规版本 PR，每类允许五个开放 PR。一次扫描可以产生数十个互相修改同一锁文件的 PR，并把数据库、缓存、向量库、运行时和模型服务的大版本迁移表现为普通单行镜像更新。依赖审计 workflow 同时在所有 PR 上运行，即使变更不涉及依赖或审计逻辑，也会下载完整 Python 与 Node.js 生产依赖。

## 决策

Python 与 npm 常规版本更新按项目聚合，只通过 `allow.update-types` 接收 patch 和 minor 更新，并设置七天冷却期与较低的开放 PR 上限。`allow.update-types` 只限制常规版本更新，不过滤需要跨 major 修复的 security updates。GitHub Actions 更新聚合为单个 PR。Docker 与 Docker Compose 的常规版本更新关闭，安全更新保持由 GitHub Dependabot security updates 产生；运行时和持久化服务升级通过显式迁移任务处理。

依赖审计 workflow 只在 shipping manifest、锁文件、审计 workflow、Makefile 或固定脆弱 fixture 变化时触发，并取消同一分支已经过期的运行。手工触发和 main 上对应变更的 push 审计保持可用；漏洞与许可证 gate 的完整决定由[依赖供应链审计门禁](2026-08-18-dependency-supply-chain-gates.md)拥有。

## 替代方案

- 保持每个依赖一个 PR：隔离清楚，但持续制造锁文件冲突和审查队列拥塞。
- 自动合并所有绿色 Dependabot PR：普通 CI 不能证明持久数据迁移、GPU 镜像、运行时大版本或真实 OCR/provider 兼容。
- 完全关闭 Dependabot：会失去安全更新入口和低风险版本漂移提示。
- 为所有 Docker 镜像维护逐项 ignore 规则：规则随镜像和版本增长，形成第二份需要人工同步的迁移清单。

## 后果

- 聚合 PR 扩大单次锁文件 diff，需要保留各项目现有 lint、unit、build 和漏洞审计 gate。
- Docker 与 Compose 的非安全版本漂移不再自动形成 PR，需要在有兼容性、生命周期或功能需求时显式规划升级。
- 路径过滤遗漏新的 manifest 会让依赖审计不自动触发；新增依赖 Owner 时必须同步更新 workflow paths。

## 验证

`scripts/test_dependency_update_policy.py` 逐个断言应用生态存在 patch/minor 分组和只影响常规更新的 allow、七天冷却期与并发上限，Docker 与 Compose 的常规 PR 上限为零，GitHub Actions 聚合，以及审计 workflow 的完整 manifest/lock 路径过滤与并发取消。测试通过临时恢复 Docker 常规 PR、删除 lock path 和把 allow 改成 ignore，证明负向案例会失败。`trust.yml` 在每个 PR 运行该测试。提交前运行工程契约、对应单元测试、YAML 解析和 `git diff --check`，并由独立 Reviewer 核对安全更新语义与路径覆盖。
