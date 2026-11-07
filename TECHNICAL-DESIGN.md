# edge-profile-mcp 技术构成

> 项目：`edge-profile-mcp`（原名 edge-automation-mcp）
> 分析日期：2026-11-08　分析方式：源码通读 + 依赖清单核对 + 实际调用验证

## 1. 定位与总体形态

这是一个**单进程、单会话、stdio 传输**的 MCP 服务器，用 Playwright（`msedge` 通道）驱动本机已安装的 Microsoft Edge。与常见浏览器 MCP 的关键差异是：浏览器运行在**独立的持久化用户配置目录**中，日常 Edge 的用户数据从不被读写。

- 使用真实 Edge 内核，不是 Playwright 内置 Chromium。
- 专用 profile 持久化；登录态跨会话保留。
- 不监听 HTTP 端口：MCP 走 stdin/stdout，无网络攻击面。
- CLI 与 MCP 两个入口各自独立启动，不共享会话。
- 每个进程最多一个活动浏览器会话、一个控制器实例。

代码规模约 1209 行（不含虚拟环境）：`browser.py` 494、`server.py` 189、`cli.py` 119、测试 378。

## 2. 分层架构

```
┌───────────────────────────────────────────────────────────────┐
│  接入层 / Presentation                                        │
│  server.py (FastMCP, stdio, 13 个 tool)   cli.py (逐行 JSON)  │
└───────────────────────────┬───────────────────────────────────┘
                            │ 相同的 operation 名称与参数
┌───────────────────────────▼───────────────────────────────────┐
│  编排层 / Orchestration                                       │
│  BrowserWorker：ThreadPoolExecutor(max_workers=1)              │
│  + threading.Lock + _closing 标志 + 错误截断(4000 字符)        │
│  负责会话唯一性、串行化、关闭顺序、进程退出清理                  │
└───────────────────────────┬───────────────────────────────────┘
                            │ 单线程调用，强制线程亲和
┌───────────────────────────▼───────────────────────────────────┐
│  浏览器领域层 / Domain                                        │
│  BrowserController：参数校验、profile 路径校验、              │
│  标签页注册表、定位器策略、输出限界、URL 校验、下载落盘          │
└───────────────────────────┬───────────────────────────────────┘
                            │ Playwright sync API
┌───────────────────────────▼───────────────────────────────────┐
│  驱动层 / Runtime                                            │
│  Playwright 1.62.0 → CDP → Microsoft Edge (channel="msedge")  │
│  launch_persistent_context(profile_dir, ...)                  │
└───────────────────────────────────────────────────────────────┘
```

横切关注点：**有界输出**、**失败即拒绝**、**线程亲和**、**尽力清理**、**数据分离**。

## 3. 模块职责

| 文件 | 行数 | 职责 | 关键实现 |
| --- | --- | --- | --- |
| `src/edge_profile_mcp/server.py` | 189 | MCP 适配器 | `FastMCP("Edge Automation")`、13 个 `@mcp.tool()`、`Image(...)`、`mcp.run(transport="stdio")`、`finally` 中 `worker.shutdown()` |
| `src/edge_profile_mcp/browser.py` | 494 | 浏览器领域逻辑 | 持久上下文启停、profile 校验、标签页、导航、ARIA 快照、截图、交互、下载 |
| `src/edge_profile_mcp/cli.py` | 119 | 命令行适配器 | 每行 `command {json}`，stdout 输出 JSON，stderr 输出错误，截图落盘 `xb` 模式 |
| `src/edge_profile_mcp/__init__.py` | 20 | 包导出 | `BrowserController`、`BrowserControlError`、`default_profile_dir`、`default_download_dir` |
| `src/edge_profile_mcp/__main__.py` | 8 | 模块入口 | `python -m edge_profile_mcp` |
| `tests/test_integration.py` | 275 | 集成测试 | 本地 `ThreadingHTTPServer` + 真实 Edge headless；含 MCP stdio 端到端 |
| `tests/test_download.py` | 103 | 下载测试 | 临时目录隔离、不覆盖同名、非下载点击报错 |

## 4. 关键调用链

一次 `navigate` 的完整路径：

```
MCP Client
  │  JSON-RPC over stdio（换行分隔）
  ▼
FastMCP lowlevel Server → Tool.call()
  │  pydantic 参数模型校验
  ▼
server.navigate(tab_id, url)          # 同步函数
  │  FastMCP 对同步工具直接调用 fn(**args)，
  │  不自动放入线程池（mcp/.../func_metadata.py:112-115）
  ▼
BrowserWorker.call()
  │  with self._lock → executor.submit(_invoke, ...)
  ▼
"edge" 专用线程 _invoke()
  ▼
BrowserController.navigate()
  │  _url() 校验 → _page() 取页 → page.goto(wait_until="domcontentloaded")
  ▼
Playwright sync API → CDP → Edge
  ▼
{"url": ..., "status": ...} → MCP structuredContent
```

异常路径：`PlaywrightError` → `_operation` 装饰器归一化为 `BrowserControlError`（4000 字符截断）→ `ToolError` → MCP 响应 `isError=true`，服务器进程不退出。

## 5. 持久化配置设计（核心差异）

`start()` 使用 `chromium.launch_persistent_context(profile_dir, ...)` 而不是 `launch()` + `new_context()`：

```
launch_persistent_context(
    profile_dir,
    channel="msedge",
    headless=...,
    timeout=...,
    accept_downloads=(download_dir is not None),
    ignore_default_args=_NORMAL_BROWSER_ARGS,   # 剔除自动化痕迹
    viewport=... | no_viewport=True,           # 有头用真实窗口
    args=["--start-maximized"],                # 仅有头
)
```

要点：

1. **首个空白页接管**。持久上下文会在 `page` 事件监听器注册前就打开一个 `about:blank`，因此启动后遍历 `context.pages` 注册，而不是再 `new_tab()`，避免出现两个标签页。
2. **`context.browser` 可用**。持久上下文区别于普通 context 的地方是它仍能拿到 `Browser` 对象，所以 `close()` 里的三级清理逻辑保持一致。
3. **profile 路径校验**（`_prepare_profile_dir`）。解析 `%VAR%` 与 `~` 后转绝对路径，`normcase` 比对黑名单；命中日常浏览器数据目录直接拒绝。
4. **进程冲突识别**（`_looks_like_profile_conflict`）。Edge 的单实例锁会使第二个实例启动失败，而 Playwright 有时只抛出 `Target page, context or browser has been closed`，因此按多个标记匹配并给出可读错误。

### 剔除的默认参数

Playwright 默认注入下列参数会显著改变窗口观感，`ignore_default_args` 将其移除：
`--enable-automation`（自动化提示条）、`--mute-audio`、`--hide-scrollbars`、`--disable-extensions`、`--disable-popup-blocking`、`--disable-back-forward-cache`、`--disable-component-extensions-with-background-pages`、`--disable-default-apps`、`--password-store=basic`、`--no-service-autorun`。

**刻意保留** `--disable-sync`：这是一项数据分离措施，防止 Microsoft 账号同步把日常书签/密码拉进自动化配置，或反之。

## 6. 工具面与数据契约

MCP 共 **13 个 tool**（`server.py`）：

| Tool | 入参 | 返回 |
| --- | --- | --- |
| `start` | `headless`, `profile_dir`, `download_dir`, `timeout_ms`, `max_tabs`, `max_snapshot_chars`, `max_screenshot_bytes` | `tab-N` 字符串 |
| `close` | — | 文本确认 |
| `list_tabs` | — | `[{id,url,title}]`（url≤8192、title≤1000） |
| `new_tab` | `url?` | `tab-N` |
| `close_tab` | `tab_id` | 文本确认 |
| `navigate` | `tab_id`, `url` | `{url,status}` |
| `snapshot` | `tab_id` | `{url,text,truncated}`，ARIA 树 |
| `screenshot` | `tab_id` | MCP `Image`（PNG，base64） |
| `click` | `tab_id` + 定位器 | 文本确认 |
| `fill` | `tab_id`, `value` + 定位器 | 文本确认 |
| `select` | `tab_id` + 定位器 + `value`/`label` 二选一 | `list[str]` |
| `press` | `tab_id`, `key` + 定位器 | 文本确认 |
| `click_and_wait_download` | `tab_id` + 定位器 + `timeout_ms` | `{path,filename,suggested_filename}` |

定位器规则：`css` 与 `role` 必须且只能二选一；`name` 只能搭配 `role`，按精确无障碍名称匹配；多元素匹配直接失败。

CLI 契约差异：`start` 返回 `{"tab_id": ...}`；`screenshot` 额外要求 `path` 并写文件；`close` 返回 `{"closed": true}`。

## 7. 配置与打包

**运行时配置**

| 设置 | 默认 | 覆盖 |
| --- | --- | --- |
| profile | `%LOCALAPPDATA%\edge-automation-mcp\profile` | `profile_dir` 参数 / `EDGE_AUTOMATION_PROFILE_DIR` |
| 下载 | 关闭 | `download_dir` 参数 / `EDGE_AUTOMATION_DOWNLOAD_DIR` |

**发布形态（双形态）**

- Python 包：`pyproject.toml`，src 布局，`requires-python >= 3.10`，入口 `edge-profile-mcp`（MCP）与 `edge-profile-cli`（CLI）。
- 插件：`.zcode-plugin/plugin.json`、`.claude-plugin/plugin.json`、`.mcp.json`（`uv run --project ${ZCODE_PLUGIN_ROOT}` 自举）、`skills/edge-browser-automation/SKILL.md`。

依赖固定为直接依赖：`mcp>=1.30.0,<2`、`playwright>=1.62.0,<2`。

## 8. 会话模型与并发模型

**生命周期**：`start` 创建 `Playwright → 持久 Context（含初始 Tab）`；重复 `start` 报错；`close` 依次关闭 context → browser → playwright，即使部分启动失败也尝试清理。

**为什么必须单线程**：Playwright 同步 API 的对象绑定创建它的线程（greenlet），跨线程会报错；而 FastMCP 对同步 tool 是在 asyncio 事件循环线程上直接调用的。三层保护：

1. `ThreadPoolExecutor(max_workers=1)` 把操作排队到唯一的 `"edge"` 线程。
2. `BrowserController._check_thread()` 记录 `_owner`，非属主线程直接拒绝。
3. `threading.Lock` + `_closing` 只保护"提交"动作；`shutdown()` 把 `close` 作为最后一个任务提交，保证 FIFO。

代价：`worker.call()` 在事件循环线程上阻塞等待，慢操作会阻塞该进程内所有请求，且不支持取消。

**标签页模型**：`_tabs: dict[str, Page]`，ID 形如 `tab-1` 单调递增；`context.on("page")` 注册弹窗并计入 `max_tabs`；`page.on("close")` 自动摘除。

## 9. 安全与边界模型

| 控制点 | 实现 |
| --- | --- |
| 数据分离 | 专用 profile 目录；日常浏览器数据目录黑名单拒绝 |
| 协议面 | 仅 stdio，无监听端口 |
| 脚本面 | 不提供任意 JS 执行；读取页面用 ARIA 快照 |
| URL 策略 | 仅绝对 `http/https`；拒绝凭据、控制字符、首尾空白、非法端口；`context.route` 中止非 http(s) 顶层导航 |
| 输出限界 | 超时 1–60000、标签页 1–100、快照 1–200000、截图 1–20000000、选择值 100 项/10000 字符、错误 4000 字符 |
| 下载 | 默认关闭；仅写入 `download_dir`；文件名净化；同名自动 `-1`、`-2`；不覆盖 |
| 明确非目标 | 不是沙箱、不是 SSRF 防护、网页内容不可信、操作有外部副作用、已登录 profile 是凭据存储 |

## 10. 测试构成

**测试金字塔**（全部针对本机真实 Edge，headless + 本地 HTTP fixture，无外网依赖）：

- `test_integration.py`：浏览器层 8 项（会话/标签页/导航/清理、交互、profile 持久化、跨 profile 隔离、日常目录拒绝、错误与限界、标签页上限、跨线程拒绝）+ MCP stdio 端到端 1 项。
- `test_download.py`：3 项（未配置目录时禁用、按目录保存且不覆盖、非下载点击报错）。

**跨进程验证**：由于 Playwright 同步 API 每进程只能有一个活动实例，"两个 profile 同时打开"必须在子进程中做（`test_other_profile_sees_no_data` 用 `subprocess.run` 启动独立解释器）。

**实测**：12/12 通过（含 MCP stdio 端到端）。

## 11. 已知约束

| 类别 | 说明 |
| --- | --- |
| 平台 | 仅 Windows；路径与隔离假设基于 Edge 的 Windows 用户数据布局 |
| 并发 | 单会话、单线程、串行；不支持多租户 |
| 取消 | 无请求取消机制 |
| 清理 | 崩溃/强杀/断电不保证清理，可能残留 Edge 进程 |
| 单实例锁 | 同一 profile 目录同时只能被一个 Edge 打开 |
| 事件循环 | 同步 tool 阻塞 FastMCP 事件循环；如需并发需改 async + `anyio.to_thread.run_sync` |

## 12. 结论

架构为清晰的三层适配器：MCP/CLI 只做协议转换，`BrowserWorker` 负责单线程串行与会话生命周期，`BrowserController` 承担全部浏览器语义与安全校验。核心取舍正确——用单专用线程换取 Playwright 线程安全，用"有界输出 + 失败即拒绝 + 无 JS 执行 + 无监听端口 + profile 路径黑名单"换取可用的安全下限，并明确声明沙箱、SSRF、提示注入等非目标。

与初版相比，主要演进是**从临时上下文改为持久化独立 profile**：获得真实浏览体验与跨会话登录保持，同时通过路径黑名单与 `--disable-sync` 维持数据分离。
