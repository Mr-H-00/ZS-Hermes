# sandbox_runner 与 workspace 行为保持型代码整理

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/workspace/filesystem.py

本决定跨越两个事实 Owner，正文分工如下：Workspace 文件边界与路径契约由 `backend/yuxi/modules/workspace/paths.py`、`filesystem.py` 与 `services/` 拥有，sandbox_runner 的装配与容器执行由 `backend/yuxi/sandbox_runner/main.py`、`manager.py`、`engine.py` 拥有；Owner 字段指向其中安全语义最集中的 no-follow 文件边界实现。

## 问题

sandbox_runner 与 workspace 两个模块经多轮功能迭代后积累了局部坏味道：公开入口之前堆放私有辅助与懒初始化代码、相邻函数重复同一段校验或清理逻辑、哨兵变量与链式三元增加阅读成本、个别魔法值缺乏具名 Owner。行为不受影响，但安全敏感的文件边界代码难以自顶向下阅读。

## 决策

### 实现方案

两个模块做一轮行为保持型整理：公开接口与主流程前置、私有实现收尾；相邻函数的重复校验与清理收敛到模块内单一私有 Owner；消除哨兵状态与魔法值。全部公开符号名、签名、异常类型与消息保持不变；no-follow、O_EXCL、0o600、advisory lock 等安全校验不削弱。

- `sandbox_runner`：`main.py` 重排为请求模型 → lifespan → app → 依赖访问器 → 路由，消除 getter 对 `app` 的前向引用；`manager.py` 命令参数校验收敛为 `_validate_command`，超时上限具名为 `MAX_TIMEOUT_SECONDS`；`engine.py` 合并 exec 流双层上下文、`__all__` 补齐 `ExecDeadlineExceeded`。
- `workspace`：`paths.py` 的 managed Workdir 校验统一错误 Owner，fd 边界错误翻译提取为 `_workspace_fd_boundary`，默认上下文文件写入消除哨兵；`filesystem.py` 暂存文件骨架收敛为 `_staged_temp_file`、分块读取统一为 `_read_bounded`；`services/` 公开用例前置，上传目标规划提取为 `_plan_upload_targets`，viewer 的授权状态透传内联。

有意的最小行为偏差共两处，均为严格改善：`download_workspace_file` 的临时文件回收改为单一 `finally`，未映射异常与请求取消时同样回收（原实现泄漏临时文件，无消费者依赖泄漏行为）；`create_viewer_directory` 响应条目改由共享 `file_entry` 装配，path 经 `PurePosixPath` 规范化（常规名称输出逐字段一致，仅 `//` 等畸形父路径回显不同）。

## 替代方案

- 不做整理，随后续功能顺手修复：坏味道分散在安全边界代码中，逐次修补更难保持语义等价，阅读成本持续累积。
- 一并改名 `MAX_WORKSPACE_UPLOAD_SIZE_BYTES` 等名实不符的公开符号：被否决，符号被路由与多个测试引用，超出行为保持范围。
- 把 `services/files.py` 中 8 处相似的异常翻译块抽成共享 contextmanager：被否决，各调用的异常集合与消息不同，参数化会隐藏每个操作的安全边界语义。

## 后果

两个模块的公开行为、API 契约、事务与并发语义不变；viewer 上传冲突预检明确只读原始 `name`，不为冲突检查构建完整条目，避免对沙盒内直接创建的非常规文件名引入规范化失败面。新增私有 helper 均有至少两个真实消费者或独立的状态/清理语义，不构成新的长期维护表面。既有未处理的坏味道（尽力取消路径的宽泛异常捕获、services 公开函数 `db`/`current_user` 参数无类型标注等）保持原样，留待有行为需求时再处理。

## 验证

- `ruff check yuxi`、`ruff format yuxi --check`、`ruff check --select I yuxi` 通过；`git diff --check` 干净。
- API 容器内 `uv run --no-sync --group test pytest test/unit -m "not slow" -q`：3314 passed、125 skipped；容器缺少仓库根文件的配置用例由宿主配置与 provider 测试补验，共 225 项通过。本地旧镜像已按当前锁文件补齐 chardet，并按 Dockerfile 从 wheel 预置 OCR 模型；全新镜像仍由发布 CI 构建验证。
- `test/integration/sandbox/test_container_sandbox.py` 在宿主真实 Docker、预构建 sandbox 镜像与独立合成目录上 26 项通过，覆盖文件字节、隔离、命令执行和清理。
- 加载当前源码后的真实 API 运行 `test_viewer_filesystem_router.py`、`test_workspace_folder_upload_api.py`、`test_workspace_runtime_cache_api.py`，连同 API Key/MCP 回归共 59 项通过，验证上传、预览、下载、文件隔离与持久结果。
- 独立 Reviewer 对完整差异和实际测试证据进行提交前审查。完整 Agent/worker E2E 由发布 CI 的 `Runtime System Tests` 验证；局部容器测试不代替该链路。

旧能力不存在：相邻函数重复的命令校验块、暂存文件骨架、分块读取循环、fd 边界错误翻译与上传目标规划的内联实现均已删除，各只剩单一实现；`_viewer_state` 透传函数与写入路径的哨兵状态不再存在。

重新引入条件：某操作需要与共享 helper 不同的暂存、读取或边界语义时，允许该处退回独立实现，但必须保留既有错误消息与安全校验契约。
