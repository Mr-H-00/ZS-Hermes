# 知识文档部分失败的任务终态

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/services/background_jobs.py

## 问题

指定文件和待处理文件的解析、入库入口捕获文件错误并保存失败计数，但正常返回会让 worker 将后台作业记录为 success。上传自动处理入口对相同情况记录 failed，任务中心的终态不能一致表达处理结果。

## 决策

### 实现方案

知识文档服务在所有文件处理结束后先通过 BackgroundJobContext 保存计数和有限文件明细。有任一文件失败时抛出包含失败数量的异常，由已有 worker 失败收尾流程保存 failed 终态。指定文件和待处理文件都识别异常及显式失败结果；参数更新失败同样按文件失败处理，不继续使用旧参数解析。全部成功和空待处理列表正常返回。

JobTracker 不解释领域结果中的 failed 字段。业务失败判定由知识文档服务拥有；worker 保留既有结果、lease 和领域 failure hook 的事务语义。

## 替代方案

- worker 根据通用 result 字段猜测失败会让作业平台依赖具体领域的结果结构。
- 新增部分成功终态需要扩展平台与客户端状态集合；现有 failed 加结果计数已经能表达失败数量和成功数量。
- 只修改任务中心文案不能修正数据库中的 success 终态。

## 后果

部分失败的任务需要查看文件明细并按文件状态重试，已成功的文件保持原有状态。结果先保存，失败收尾不覆盖明细。取消和租约丢失继续由已有控制检查处理，不新增自动重试。

## 验证

- 参数化 unit 覆盖解析／入库、指定文件／待处理文件、异常／显式失败结果／参数更新失败／全部成功，验证失败数量、成功数量和文件明细。
- 真实 PostgreSQL 隔离 Schema integration 调用生产 worker 入口及真实注册 Handler，重新读取作业行，验证四条路径都记录 failed、清除执行 Owner 并保留结果。文件处理边界使用确定性 stub，不调用外部解析和 embedding 服务；未验证真实 ARQ 投递和供应商失败链路。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_background_job_repository.py -k document_partial_failure -q -p no:cacheprovider`：4 passed。
- 在独立 Python 进程加载修复前 Owner，保留新增测试验证负向案例：12 failed、4 passed，失败落在未抛出失败、参数更新错误被忽略和失败计数断言；不改写工作区源码。

关联问题：[xerrors/Yuxi #1095](https://github.com/xerrors/Yuxi/issues/1095)。
