# Yuxi 文档站启动

Yuxi 文档站位于 `docs/` 目录，使用 VitePress 构建。

## 前置条件

- Node.js 22.13 或更高版本
- pnpm
- 已进入仓库根目录：`E:\pythonProject\Yuxi`

项目声明的包管理器版本为 `pnpm@11.24.0`，该版本需要 Node.js 22.13 或更高版本。可以先检查当前 Node.js，再确认项目实际使用的 pnpm 版本：

```powershell
node --version
pnpm --dir docs exec pnpm --version
```

使用 NVM for Windows 时，先查看已安装版本，再切换到满足要求的版本。例如本机已安装 Node.js 24.10.0 时：

```powershell
nvm list
nvm use 24.10.0
node --version
```

`nvm use` 成功后应输出 `Now using node v24.10.0 (64-bit)`，且 `node --version` 应输出 `v24.10.0`。

如果本机尚未安装 pnpm，可以使用 Corepack：

```powershell
corepack enable
corepack install --global pnpm@11.24.0
```

## 安装依赖

在仓库根目录执行：

```powershell
pnpm --dir docs install
```

依赖只安装到 `docs/node_modules/`，该目录属于本地构建目录，不需要提交到 Git。

## 启动开发站点

开发模式支持 Markdown、配置和 SVG 修改后的热更新：

```powershell
pnpm --dir docs run dev
```

启动后打开：

```text
http://127.0.0.1:5173/Yuxi/
```

系统架构页面地址：

```text
http://127.0.0.1:5173/Yuxi/mechanisms/system-architecture.html
```

如果需要让同一局域网中的其他设备访问，可以监听所有网卡：

```powershell
pnpm --dir docs run dev --host 0.0.0.0
```

然后使用本机局域网 IP 访问：

```text
http://<本机局域网IP>:5173/Yuxi/
```

停止开发服务器时，在运行它的终端按 `Ctrl+C`。

## 构建静态站点

发布或检查生产构建时执行：

```powershell
pnpm --dir docs run build
```

构建产物输出到：

```text
docs/.vitepress/dist/
```

构建过程会检查 Markdown、站内链接、导航和静态资源引用。构建成功不代表 PostgreSQL、Redis、worker、SSE、MinIO、Milvus、Neo4j 或 Sandbox 真实链路已经验证。

## 预览生产构建

先构建，再启动预览服务：

```powershell
pnpm --dir docs run build
pnpm --dir docs run preview
```

默认访问地址：

```text
http://127.0.0.1:4173/Yuxi/
```

## 常用命令

| 目的 | 命令 |
| --- | --- |
| 安装依赖 | `pnpm --dir docs install` |
| 启动开发模式 | `pnpm --dir docs run dev` |
| 监听局域网 | `pnpm --dir docs run dev --host 0.0.0.0` |
| 构建站点 | `pnpm --dir docs run build` |
| 预览构建产物 | `pnpm --dir docs run preview` |

## 常见问题

### PowerShell 无法识别 node.exe

如果执行 pnpm 时在 `pnpm.ps1` 中出现以下错误：

```text
无法将“node.exe”项识别为 cmdlet、函数、脚本文件或可运行程序的名称
```

这表示 pnpm 启动脚本存在，但当前 PowerShell 找不到 Node.js。如果使用 NVM for Windows，执行：

```powershell
nvm list
nvm current
nvm use 24.10.0
node --version
```

`nvm current` 输出 `No current version` 时，必须先执行 `nvm use <已安装版本>`。如果 `nvm use` 成功后当前终端仍找不到 `node.exe`，关闭该 PowerShell 窗口，新建一个终端后重新执行 `node --version`。

### pnpm 报错 No such built-in module: node:sqlite

该错误表示当前 Node.js 版本过低，无法运行项目声明的 `pnpm@11.24.0`。先切换到 Node.js 22.13 或更高版本：

```powershell
nvm list
nvm use 24.10.0
node --version
pnpm --dir docs exec pnpm --version
```

确认 Node.js 版本满足要求、项目中的 pnpm 版本为 `11.24.0` 后，重新启动：

```powershell
pnpm --dir docs run dev
```

### 访问根地址显示 404

当前站点配置了 `/Yuxi/` base 路径，不能只访问：

```text
http://127.0.0.1:5173/
```

应访问：

```text
http://127.0.0.1:5173/Yuxi/
```

### pnpm 提示版本不匹配或尝试联网下载

先确认项目实际使用的版本：

```powershell
pnpm --dir docs exec pnpm --version
```

如果不是 `11.24.0`，安装项目声明的版本：

```powershell
corepack enable
corepack install --global pnpm@11.24.0
```

网络受限时，安装阶段可能出现 `ERR_PNPM_META_FETCH_FAIL`。此时需要在可访问 npm registry 的环境中完成依赖安装，或者使用已经准备好的 `docs/node_modules/`。

### 修改后页面没有更新

确认修改的是 `docs/` 下的源文件，并检查开发服务器终端是否仍在运行。静态资源放在：

```text
docs/public/
```

例如，架构图资源位于：

```text
docs/public/architecture/
```

修改 `docs/.vitepress/config.mts` 后，如果热更新没有生效，可以停止并重新执行：

```powershell
pnpm --dir docs run dev
```
