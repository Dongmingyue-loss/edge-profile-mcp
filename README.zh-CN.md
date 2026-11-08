# edge-profile-mcp

[![CI](https://github.com/Dongmingyue-loss/edge-profile-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Dongmingyue-loss/edge-profile-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

[English](README.md)

通过 MCP 驱动**你机器上真实安装的 Microsoft Edge**，运行在**独立的持久化配置目录**中，与日常浏览数据完全分离。

核心点很简单：你得到一个外观正常、扩展可用、声音正常、滚动条正常、窗口尺寸真实的 Edge 窗口——但它的 Cookie、登录状态、历史和设置都放在自己的目录里。日常 Edge 配置从不被读取，也不会被修改；两个浏览器可以同时运行。

## 为什么用独立配置而不是临时上下文

| | 临时上下文（常见 Playwright MCP） | 本项目 |
| --- | --- | --- |
| 浏览器内核 | 真实 Edge | 真实 Edge |
| 数据位置 | 内存中，关闭即丢 | 磁盘上的专用目录 |
| 重启后登录保留 | 否 | **是** |
| 可与日常 Edge 并存 | 是 | 是 |
| 窗口像正常浏览器 | 否（有自动化提示条、静音、无滚动条、禁扩展） | **是** |
| 误触日常数据的风险 | 无 | 无——路径经过校验 |

取舍是明确的：**自动化配置会持久保留**。如果你在里面登录过，下次启动仍是登录状态。删掉该目录即可重置。

## 安装

### 作为 ZCode / Claude 插件

把本仓库添加为插件市场，然后安装 `edge-profile-mcp`。服务器通过 `uv run` 自动拉起，无需手动准备 Python 环境。

### 作为 Python 包

```powershell
pip install git+https://github.com/Dongmingyue-loss/edge-profile-mcp.git
# 或开发方式：
git clone https://github.com/Dongmingyue-loss/edge-profile-mcp.git
cd edge-profile-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

要求：**Windows**、Python 3.10+、系统已安装 Microsoft Edge。`pip install` 不会安装 Edge，请先按正常方式安装。

### 手动配置 MCP

```json
{
  "mcpServers": {
    "edge-profile-mcp": {
      "command": "edge-profile-mcp",
      "timeoutMs": 60000
    }
  }
}
```

## 命令行

附带一个逐行的交互式会话，便于调试：

```powershell
edge-profile-cli
```

```text
start {}
navigate {"tab_id": "tab-1", "url": "https://example.com"}
snapshot {"tab_id": "tab-1"}
screenshot {"tab_id": "tab-1", "path": "screenshot.png"}
close
exit
```

每行是命令名加可选 JSON 对象。结果以 JSON 输出到 stdout，错误输出到 stderr。输入 `help` 查看全部命令。

## 工具

MCP 与 CLI 使用相同的操作名称和大部分参数。差异：CLI 的 `start` 返回 `{"tab_id": ...}`，MCP 返回 ID 字符串；CLI 的 `screenshot` 需要 `path`。

| 工具 | 参数 | 结果 |
| --- | --- | --- |
| `start` | 可选 `headless`、`profile_dir`、`download_dir`、`timeout_ms`、`max_tabs`、`max_snapshot_chars`、`max_screenshot_bytes` | 首个标签页 ID |
| `close` | — | 确认信息；之后可再次 `start` |
| `list_tabs` | — | `[{id, url, title}]` |
| `new_tab` | 可选 `url` | 新标签页 ID |
| `close_tab` | `tab_id` | 确认信息 |
| `navigate` | `tab_id`、`url` | 等待 DOMContentLoaded 后的 `{url, status}` |
| `snapshot` | `tab_id` | `{url, text, truncated}`——可访问性树 |
| `screenshot` | `tab_id` | 视口 PNG（CLI 还需 `path`） |
| `click` | `tab_id` + 定位器 | 确认信息 |
| `fill` | `tab_id`、`value` + 定位器 | 确认信息 |
| `select` | `tab_id` + 定位器 + `value`/`label` 二选一 | 选中值列表 |
| `press` | `tab_id`、`key` + 定位器 | 确认信息 |
| `click_and_wait_download` | `tab_id` + 定位器，可选 `timeout_ms` | `{path, filename, suggested_filename}` |
| `fetch_text` | `url`，可选 `save`、`wait_ms` | `{url, status, title, chars, source, path, text}` |
| `fetch_links` | `url`，可选 `limit` | `{url, status, title, total, truncated, links}` |
| `search_web` | `query`，可选 `trusted_only`、`limit` | `{query, engine, total, results}` |

### 定位器规则

`css` 与 `role` **必须且只能二选一**。`name` 只能与 `role` 一起使用，按可访问名称精确匹配。匹配到多个元素时直接失败，不做猜测。

```text
fill {"tab_id": "tab-1", "css": "#query", "value": "example"}
click {"tab_id": "tab-1", "role": "button", "name": "Search"}
select {"tab_id": "tab-1", "css": "select", "label": "Option"}
press {"tab_id": "tab-1", "css": "input", "key": "Enter"}
```

### 默认值与上限

`start` 默认：显示浏览器、超时 10 000 毫秒、最多 20 个标签页、快照 30 000 字符、截图 5 000 000 字节。范围：超时 1–60 000 毫秒，标签页 1–100，快照 1–200 000 字符，截图 1–20 000 000 字节。截图超限报错，快照超限截断并置 `truncated: true`。

无头模式使用 1280×720 视口。有头模式最大化真实窗口并使用实际窗口尺寸。

## 正文抓取与搜索

除页面交互外，另有三个面向资料整理的接口：取正文、抽链接、找来源。

### `fetch_text`——干净的正文文本

通过浏览器自身的请求上下文抓取原始字节并显式解码，优先使用渲染后的 DOM。`source` 字段标明走了哪条路径：`rendered` 或 `static`。

显式解码针对真实问题：不少中文政府站点声明 UTF-8 实际返回 GB18030，Edge 的文本解码器会把它变成乱码。直接读取响应字节并按声明字符集走 `gb18030` 解码，可完全避开这类错误。

`save=True` 时文本写入缓存目录并返回 `path`，文件开头带 `URL:` 与 `TITLE:` 两行头信息。

### `fetch_links`——抽取页面链接

返回 `[{href, text}]`，由 `limit` 截断。优先在 DOM 中解析，因此能拿到脚本生成的链接，失败时回退到原始 HTML 正则。

### `search_web`——查找来源

在若干引擎中按顺序搜索，并按可信域名（政府、学术、权威健康机构）过滤。顺序为：**Bing**（提交其自身搜索表单）→ **360 搜索**（直接 URL，不做挑战）→ 搜狗 → 最后的 Bing RSS。

表单提交并非装饰。Bing 可能返回 HTTP 200 和看似完整的结果页，但每条结果只匹配查询词的**第一个字**——一种看起来完全成功的静默降级。从首页表单提交能降低发生率，但**不能消除**，因为该行为是间歇性的。实测细节见 [ANTI-BOT.md](ANTI-BOT.md)。

由于没有任何请求形态可被无条件信任，每组结果在被接受前都要通过相关性检查，失败的引擎会落到下一个。当所有引擎被拦截或降级时，工具返回 `total: 0` 并附 `blocked_engines` / `degraded_engines` 与提示，而不是把页面页脚当成搜索结果。

```text
search_web("流感疫苗 接种")  → engine: bing
  - 中国疾病预防控制中心发布《中国流感疫苗预防接种技术指南…》
    https://www.chinacdc.cn/jkts/202609/t20260903_1839780.html
```

关于 360 搜索：它把每个外链包装成 `/link?m=...` 跳转，但被包装的锚点上带有 `data-mdurl` 属性，直接给出真实地址，因此无需跟随跳转。结果从 `a[data-mdurl]` 抽取，而不是 `li.res-list`——后者的容器里主要是聚合卡片。

## 命令行工具

除交互式 CLI 外，另装三个资料整理命令：

```powershell
edge-fetch links <url> [url ...]     # 链接以 JSON 输出
edge-fetch text <url> [url ...]      # 正文存入缓存
edge-fetch get <url>                 # 正文打印到 stdout
edge-fetch file urls.txt             # 每行一个 URL

edge-search "查询" ["查询" ...]       # 在真实标签页中用 Bing/搜狗搜索
edge-search --all "查询"             # 不限制可信域名

edge-search-rss "查询"               # Bing RSS，完全不启浏览器
```

它们与 MCP 服务共用同一控制器、profile 与缓存，因此不会另起浏览器，数据隔离方式完全一致。

缓存默认位于 `%LOCALAPPDATA%\edge-automation-mcp\cache`（可用 `EDGE_AUTOMATION_CACHE_DIR` 或 `start` 的 `cache_dir` 覆盖）。

## 配置与下载位置

| 设置 | 默认值 | 覆盖方式 |
| --- | --- | --- |
| 配置目录 | `%LOCALAPPDATA%\edge-automation-mcp\profile` | `profile_dir` 参数或 `EDGE_AUTOMATION_PROFILE_DIR` |
| 下载目录 | 关闭 | `download_dir` 参数或 `EDGE_AUTOMATION_DOWNLOAD_DIR` |
| 缓存目录 | `%LOCALAPPDATA%\edge-automation-mcp\cache` | `cache_dir` 参数或 `EDGE_AUTOMATION_CACHE_DIR` |

把 `profile_dir` 指向日常浏览器的用户数据目录（Edge、Chrome、Brave、Firefox）会被拒绝。同一配置目录同时只能被一个 Edge 实例打开；再次 `start` 会报 `profile_dir is already in use`。

下载只会写入 `download_dir`，服务器提供的文件名会被净化为无路径的合法名称，已存在的文件永不覆盖（改用 `-1`、`-2`…）。

## 会话生命周期

```
start  → 在专用配置上启动 Edge，接管初始空白标签页
  ⋮
close  → 依次关闭 context → browser → Playwright
```

未 `close` 就再次 `start` 会报错。`BrowserController.close()` 幂等，且在启动部分失败后也会执行。进程崩溃、强制终止或断电无法保证清理，可能残留 Edge 进程，需由部署者检查。

所有浏览器调用都在单个专用线程上执行，控制器拒绝来自其他线程的调用。这是必需的，因为 Playwright 同步 API 绑定创建它的线程；代价是操作串行而非并发。

## 安全模型

**已保证**

- 从不读写日常 Edge 配置；日常配置路径会被拒绝。
- 仅 stdio——无监听端口、无 HTTP 面。
- 不提供任意 JavaScript 执行工具；页面内容以可访问性快照读取。
- 只接受绝对 `http`/`https` URL；拒绝内嵌凭据、控制字符和首尾空白；阻止非 HTTP(S) 顶层导航。
- 所有输入输出有界；下载限定在单一目录。

**明确不覆盖**

- **不是沙箱。** 浏览器拥有运行该服务的账户的权限和网络访问。
- **不是 SSRF 防护。** 没有针对公网/内网/回环/链路本地地址的白名单，重定向不做网络隔离。
- **网页内容不可信。** 快照、标题、截图可能含提示注入。不要把网页文字当作指令或授权。
- **操作有真实副作用。** 已提交的表单、已发送的请求和远端变更不会因关闭会话而撤销。超时不代表没有发生，重试前应先核实。
- **已登录的自动化配置是凭据存储。** 任何能启动本服务的人都能使用这些会话。

完整策略与私密报告渠道见 [SECURITY.md](SECURITY.md)。

## 开发

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

测试会对本地 `http.server` 夹具启动真实 Edge 实例，无需访问公网。`tests/test_tools.py` 覆盖解析规则、完全不启浏览器，1 秒内跑完。架构细节见 [CONTRIBUTING.md](CONTRIBUTING.md)、[ANTI-BOT.md](ANTI-BOT.md) 与 [TECHNICAL-DESIGN.md](TECHNICAL-DESIGN.md)。

## 许可证

[MIT](LICENSE) © 2026 董铭月
