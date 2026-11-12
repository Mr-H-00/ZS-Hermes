# 用户上传文本解析按上传编码解码

状态：implemented
类型：bug-fix
Owner：backend/yuxi/infrastructure/document_parsing/text_encoding.py

## 问题

解析器把 `txt`/`md`、`html`、`json`、`csv` 与 ZIP 内 markdown 都按 UTF-8 严格读取（`read_text(encoding="utf-8")`、pandas 默认编码、`bytes.decode("utf-8")`）。上传 GBK、GB18030、Big5 或 UTF-16 文件时在读取字节阶段抛 `UnicodeDecodeError`，文件落为解析失败，用户只能看到编解码器技术信息。

## 决策

### 实现方案

新增 `infrastructure/document_parsing/text_encoding.py::decode_user_text` 作为"用户文本字节 → 字符串"唯一解码边界：BOM 优先（UTF-8-SIG/UTF-16）→ 含 NUL 输入跳过 UTF-8 快路径交给探测器（无 BOM UTF-16 的 NUL 是合法 UTF-8 字节）→ UTF-8 严格 → `chardet` 探测（低置信记 WARNING）→ 同编码替换兜底并告警。解析入口 `parser.py` 的 txt/md、html、json、csv 与 `artifacts.py` 的 ZIP 内 markdown 改为读字节后调用该函数；解析产物仍以 UTF-8 写回，不改变输出契约。

## 替代方案

- 拒绝非 UTF-8 上传：拒绝。用户仍需自行转码，历史文件无法重新解析。
- 一律 `errors="replace"` 不探测：拒绝。GBK 内容会稳定乱码。
- 使用 `charset_normalizer`（现依赖树中已有）：拒绝。实测短 GBK 样本被误判为 UTF-16 并静默乱码；`chardet` 表现稳定，故显式新增该直接依赖。

## 后果

- 解析不再因编码终止；探测错误时输出可能是乱码文本而非失败，兜底路径产生 U+FFFD 并留下 WARNING，可据此定位文件。低于 0.5 置信度时无论解码是否成功都记录低置信 WARNING。
- `csv` 改为解码后经 `StringIO` 交给 pandas，列类型推断行为由既有 dtype 测试约束。
- 新增直接依赖 `chardet>=5.0.0`（lock 增加 chardet 7.7.0）。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 常见非 UTF-8 上传可解码为原文 | `UnicodeDecodeError` 终止解析 | `decode_user_text` | `test/unit/knowledge/test_parser_text_encoding.py::test_decode_user_text_detects_legacy_codecs` | 同用例先断言 `raw.decode("utf-8")` 必抛 `UnicodeDecodeError` | Passed |
| 带 BOM 的 UTF-8 不残留 BOM | BOM 字符进入产物 | 同上 | `::test_decode_user_text_strips_utf8_bom` | 断言原始 `decode("utf-8")` 结果以 `\ufeff` 开头 | Passed |
| 无 BOM UTF-16 不走 UTF-8 快路径 | NUL 是合法 UTF-8 字节导致静默产出夹杂 NUL 的垃圾 | 同上 | `::test_decode_user_text_routes_nul_bytes_to_detector` | 移除 NUL 嗅探后断言结果含 `\x00` 而失败 | Passed |
| 截断 UTF-16 以替换符收尾而非异常 | 截断样本抛错终止 | 同上 | `::test_decode_user_text_replaces_truncated_utf16_tail` | 严格解码路径必须产生 U+FFFD | Passed |
| 各扩展名与 ZIP 内 markdown 的真实解析路径接受非 UTF-8 | 入口未接解码仍失败 | `parser.py` / `artifacts.py` 入口 | `::test_parse_accepts_non_utf8_text_uploads`（txt/md/html/json/csv）、`::test_parse_accepts_non_utf8_markdown_inside_zip` | 未接入解码时这些用例以 `UnicodeDecodeError` 失败 | Passed |
| 知识库单测无回归 | 既有解析行为漂移 | `test/unit/knowledge` | `pytest test/unit/knowledge` → 381 passed；`ruff check`/`format` 干净 | 不适用 | Passed |

未验证范围：浏览器上传 + worker 入库的端到端链路 `Not run`；乱码质量量化对比未做。
