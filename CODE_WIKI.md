# 流水自动导出工具 v1.0 — Code Wiki

> 本文档是对项目代码的完整解读，涵盖整体架构、模块职责、关键类与函数、依赖关系与运行方式，供二次开发与维护参考。

***

## 目录

- [1. 项目概述](#1-项目概述)

- [2. 整体架构](#2-整体架构)

- [3. 目录结构](#3-目录结构)

- [4. 主要模块职责](#4-主要模块职责)

- [5. 关键类与函数](#5-关键类与函数)

- [6. 依赖关系](#6-依赖关系)

- [7. 核心流程解析](#7-核心流程解析)

- [8. 平台插件体系](#8-平台插件体系)

- [9. 项目运行方式](#9-项目运行方式)

- [10. 数据目录与文件约定](#10-数据目录与文件约定)

- [11. 附录：文件清单](#11-附录文件清单)

***

## 1. 项目概述

| 项目   | 说明                                                                                |
| ---- | --------------------------------------------------------------------------------- |
| 名称   | 流水自动导出工具（Liushui Export Tool）v1.0                                                 |
| 定位 | 面向**非技术业务用户**的可视化桌面工具，自动批量导出电商/支付平台的交易流水（**当前已实现：有赞**；其余平台为待实现的开发模板，可通过插件体系补充） |
| 技术栈  | Python 3.10+、Tkinter（GUI）、Playwright / Chromium（浏览器自动化）                           |
| 架构风格 | **插件化**：每个平台一个独立文件夹，程序启动时自动发现加载，新增平台无需修改主程序                                       |

核心特性：

- **智能定位导出**：按 placeholder 文字/按钮文本自动识别页面上的日期输入框、查询按钮、导出按钮，不依赖平台特定选择器（`core/exporters.py` 的 `SmartExporter`）；

- **登录态隔离**：为每个「平台 + 商户」建立独立浏览器数据目录，登录状态按商户持久化保存（1\~7 天）；

- **下载兜底机制**：事件队列 + 目录轮询双通道捕获浏览器下载，防漏、防残留（UUID 文件自动归位）；

- **文件归档**：下载文件按 `平台/商户/日期范围` 组织，同名旧文件自动归档至 `历史/` 子目录；

- **高容错**：浏览器启动失败自动清理 Chrome 锁文件并重试 3 次；任何异常均写入日志并以弹窗提示，不静默崩溃。

***

## 2. 整体架构

项目采用三层结构，从上到下依次为 **GUI 表现层 → 核心框架层 → 平台插件层**：

```
┌─────────────────────────────────────────────────────────────────────────┐
│  GUI 表现层                                                            │
│  core/main_gui.py  (LiushuiApp, Tkinter)                               │
│  └─ 平台/商户勾选 · 日期选择 · 操作历史 · 实时日志 · 进度条 · 状态标识      │
├─────────────────────────────────────────────────────────────────────────┤
│  核心框架层  (全部位于 core/ 包内)                                       │
│  ├─ core/browser.py     BrowserManager: 浏览器生命周期/导航/点击/填表/   │
│  │                     下载捕获/文件归档/弹窗处理/日期选择器/重试容错      │
│  ├─ core/exporters.py   SmartExporter: 智能定位自动导出(通用兜底导出器)    │
│  ├─ core/loader.py      discover_platforms: 扫描加载 platforms/ 插件     │
│  ├─ core/platform_base.py  PlatformBase: 平台抽象基类(元信息+接口约定)    │
│  ├─ core/config.py      全局配置常量 + 用户设置(settings.json)读写         │
│  └─ core/logger.py      日志(文件 + GUI 回调双通道)                       │
├─────────────────────────────────────────────────────────────────────────┤
│  平台插件层  platforms/<platform>/export.py  (每个平台一个文件夹)         │
│  当前已实现: youzan(有赞)  其余平台为待实现模板                           │
└─────────────────────────────────────────────────────────────────────────┘
```

关键设计决策：

1. **平台即插即用**：`core/loader.py` 扫描 `platforms/` 下含 `export.py` 的文件夹，导入模块并找出继承 `PlatformBase` 的子类进行实例化，以 `key` 为索引注册。新增平台 = 新建文件夹 + 一个类，主程序零改动。
2. **浏览器能力下沉**：平台脚本不直接操作 Playwright，而是调用 `BrowserManager` 提供的 10+ 个用户友好辅助方法（`click_text`、`fill_placeholder`、`wait_download` 等），平台改版只需改脚本，浏览器层稳定复用。
3. **登录态按商户隔离**：`BrowserManager.set_browser_profile(platform_key, merchant)` 生成 `browser_data/平台key/商户/` 独立 profile，同一商户的登录状态不互相污染。
4. **下载双通道捕获**：`context.on("download")` 事件队列 + downloads 根目录文件轮询兜底，保证"点了下载但事件丢失"的场景仍能拿到文件。

***

## 3. 目录结构

```
liushui_export/
├── requirements.txt         # Python 依赖清单（playwright==1.62.0）
├── settings.json            # 用户设置（运行时自动生成/更新）
├── start.bat                # 命令行启动脚本（控制台常驻，错误可见）
├── 启动工具.vbs             # 免控制台启动脚本（pythonw，首次自动装依赖）
├── 使用说明.md              # 面向最终用户的使用文档
├── 脚本编写指南.md          # 面向开发者的平台插件编写指南
├── core/                    # ★ 核心框架包（GUI + 浏览器 + 导出器 + 配置 + 日志）
│   ├── __init__.py
│   ├── main_gui.py          # 主程序入口（Tkinter GUI）
│   ├── browser.py           # 浏览器管理（BrowserManager）
│   ├── exporters.py         # 智能导出器（SmartExporter）
│   ├── config.py            # 全局配置与用户设置读写
│   ├── logger.py            # 日志模块
│   ├── loader.py            # 平台加载器（自动发现）
│   └── platform_base.py     # 平台基类
├── platforms/               # ★ 平台插件目录
│   ├── __init__.py
│   └── youzan/        (export.py + __init__.py)   # 当前唯一已实现平台
├── downloads/               # 运行时自动创建：导出文件归档目录
├── browser_data/            # 运行时自动创建：浏览器登录态数据
│   └── <平台key>/<商户名>/  # 每商户一个 profile
└── logs/                    # 运行时自动创建：运行日志 + 操作历史
```

***

## 4. 主要模块职责

| 模块/文件                    | 职责                                                                                                                                                                    |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `core/main_gui.py`       | **GUI 主程序**。构建三栏界面（左平台/商户选择、中设置+操作历史+按钮、右日志+进度），管理任务线程、浏览器实例生命周期，编排「首次登录 / 导出流水 / 检查状态」三大业务流程，导出后自动汇总文件并打开文件夹。                                                        |
| `core/browser.py`        | **浏览器管理**。封装 Playwright 持久化上下文（含登录态），提供导航/点击/填表/等待/下载捕获/文件归档/弹窗关闭/日期选择器辅助方法；内置浏览器启动重试与 Chrome 锁文件清理。                                                                  |
| `core/exporters.py`      | **智能导出器**。`SmartExporter` 通过 placeholder 匹配日期输入框、按文本匹配查询/导出按钮，全自动完成「填日期→查询→导出→等待下载」，失败时截图并返回提示，是平台未自定义 `export()` 时的默认实现。                                             |
| `core/loader.py`         | **平台加载器**。扫描 `platforms/` 自动发现并实例化所有平台，返回 `{key: 平台实例}` 字典。                                                                                                           |
| `core/platform_base.py`  | **平台抽象基类**。定义平台元信息（`key/name/login_url/export_url/guide/enabled`）与接口约定（`login()`、`export()`），默认 `login()` 打开登录页，默认 `export()` 走 `SmartExporter`。                      |
| `core/config.py`         | **全局配置**。`ROOT_DIR`（项目根）、`DOWNLOAD_DIR`（下载目录）、`BROWSER_DATA_DIR`（浏览器数据目录）、`SETTINGS_FILE`、`DEFAULT_SETTINGS`；提供 `load_settings()/save_settings()` 读写 `settings.json`。 |
| `core/logger.py` | **合并后的唯一日志通道**。统一输出控制台、`logs/run_YYYYMMDD.log` 与 GUI 回调；`log(msg, level, callback)` 支持级别（warning/error 界面行带 `[WARN]/[ERROR]` 前缀），系统日志与用户操作统一记录这个通道。 |
| `core/keepalive.py` | **登录保活服务**。`KeepAliveService`（后台线程）按可配置间隔周期巡检已选商户的登录态（打开 `login_url` 判断会话），任务执行中自动跳过本轮；浏览器工厂可注入便于测试。 |
| `platforms/*/export.py`  | **平台插件**。每个文件定义一个继承 `PlatformBase` 的导出类，实现该平台的登录与导出流程；平台专项逻辑（如有赞的 URL 日期参数）直接写在平台脚本内，不放入 `core/`（见 [第 8 章](#8-平台插件体系)）。 |
| `start.bat` / `启动工具.vbs` | **启动脚本**。以 `python -m core.main_gui` 方式启动：前者用控制台 Python（错误可见）；后者用 `pythonw` 免控制台，并在首次运行时自动 `pip install playwright` + 安装 Chromium。                                    |

***

## 5. 关键类与函数

### 5.1 `LiushuiApp`（core/main\_gui.py）— GUI 主应用类

构造时依次执行：发现平台（`discover_platforms`）→ 读取设置 → 发现商户（`discover_merchants`）→ 建窗 → 构建右/左/中三栏 → 刷新概览条。维护关键状态：`browser`（当前浏览器实例）、`running`（任务锁）、`platform_vars/merchant_vars`（勾选状态）、`_login_confirm`（登录确认事件）。

#### 界面构建方法

| 方法                      | 说明                                                                                                              |
| ----------------------- | --------------------------------------------------------------------------------------------------------------- |
| `_setup_window()`       | 设置 1100x700、最小 900x550、默认最大化。                                                                                   |
| `_build_left_panel()`   | 左侧面板：平台勾选（联动商户）、商户数徽标、状态指示灯、`+` 添加商户按钮；商户子勾选框容器（可滚动 Canvas）。                                                    |
| `_build_right_panel()`  | 右侧面板：操作日志 `ScrolledText`（深色主题）+ 状态栏 + 确定性进度条。                                                                   |
| `_build_middle_panel()` | 中间面板：操作历史（跨启动累积）、导出日期（起止 + 昨天/近7天/近30天快捷）、显示浏览器开关、文件名模式单选（统一命名/保留原文件名）、概览条、按钮区（首次登录/检查状态/打开下载/使用说明 + 大号「开始导出」）。 |

#### 用户操作入口

| 方法                                     | 说明                                                           |
| -------------------------------------- | ------------------------------------------------------------ |
| `_action_login_all()`                  | 校验日期/选择后启动线程执行 `_do_login`，并记录操作历史。                          |
| `_action_export_all()`                 | 校验后弹出任务确认框（平台·商户明细 + 日期），确认后启动 `_do_export`。                 |
| `_action_check_status()`               | 启动线程执行 `_do_check`，逐个平台通过 URL/标题判断登录态。                       |
| `_action_open_folder()`                | 打开 `downloads/` 目录。                                          |
| `_action_help()`                       | 弹出简化使用说明。                                                    |
| `_prompt_add_merchant(key, plat_name)` | 弹窗输入商户名 → 清洗非法字符 → 创建 `browser_data/平台key/商户/` 目录 → 动态刷新勾选框。 |

#### 核心业务流程方法

| 方法                                 | 说明                                                                                                                                                                                                                    |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_do_login(selected)`              | **首次登录**：遍历选中的平台×商户，强制可见窗口启动独立 profile 浏览器 → `plat.login(browser)` 打开登录页 → 主线程弹窗「请在浏览器完成登录」→ 后台线程阻塞等待用户点确定（600s 超时）→ 关闭浏览器进入下一个。                                                                                      |
| `_do_export(selected)`             | **导出流水**：遍历任务，`_ensure_browser` 启动独立 profile 浏览器 → `set_export_context(平台, 起, 止, 商户)` 设置下载归档上下文 → `plat.export(browser, start, end)` → 按返回值 `success/manual/其他` 更新平台状态灯并统计成败；结束后调用 `_copy_export_outputs` 汇总并自动打开文件夹。 |
| `_do_check(selected)`              | **检查登录态**：逐个启动浏览器访问 `login_url`，延迟 3s 后通过 `get_page_info()` 判断 URL 含 "login" 或标题含 "登录" 则未登录，否则已登录。                                                                                                                    |
| `_copy_export_outputs(start, end)` | 导出后把各平台/商户日期目录下的最新文件去重复制到 `downloads/开始日期_结束日期_时间戳/` 汇总文件夹，兼容新旧两种目录结构（平台/日期 与 平台/商户/日期）。                                                                                                                              |

#### 任务线程管理

| 方法                                               | 说明                                                                                                        |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| `_run_async(target)`                             | 任务互斥锁（运行中则警告）；将所有平台状态灯复位为 idle 后启动后台守护线程。                                                                 |
| `_thread_wrapper(target)`                        | 线程入口：执行任务 → 捕获异常写入日志 → `finally` 重置运行锁/进度条并**自动关闭浏览器**（避免残留进程）。                                           |
| `_ensure_browser(plat, merchant, force_visible)` | 关闭旧浏览器 → 按 headless 配置创建 `BrowserManager` → 设置平台+商户 profile → `start()`。登录流程 `force_visible=True` 强制显示窗口。 |

#### 状态/日志

| 方法                                  | 说明                                                                                 |
| ----------------------------------- | ---------------------------------------------------------------------------------- |
| `_append_log(line)`                 | 追加日志到右侧日志区，最多保留 300 条。                                                             |
| `_set_status(text)`                 | 更新右侧状态栏。                                                                           |
| `_set_platform_status(key, status)` | 置平台状态灯颜色：`ok`绿 / `warn`橙 / `error`红 / `idle`灰。                                     |
| `_refresh_summary()`                | 实时刷新概览条「已选 X 个商户 · Y 个平台 \[日期]」。                                                   |

#### 其他

- `_validate_dates()`：校验日期格式（`YYYY-MM-DD`）与起止大小关系。

- `_get_selected()` / `_get_selected_merchants(key)`：取勾选的平台 key / 商户名列表。

- `_sanitize_name(name)`：正则清洗路径非法字符 `[<>:"/\\|?*]` 及控制字符。

- 模块级函数 `check_dependencies()`：检测 `playwright`，缺失时询问并自动 `pip install playwright` + `playwright install chromium`，完成后重启进程。

- 模块级函数 `main()`：入口；全局异常兜底（写日志 + 弹窗提示），防止程序静默崩溃。

### 5.2 `BrowserManager`（core/browser.py）— 浏览器管理类

封装 Playwright 的**持久化上下文**（`launch_persistent_context`，`user_data_dir` 按平台/商户隔离），是平台脚本与浏览器之间的唯一桥梁。构造时初始化下载捕获状态机（`_dl_queue` 事件队列、`_dl_capture_on` 捕获开关、`_dl_temp_dir` 临时目录）。

#### 生命周期与重试

| 方法                                   | 说明                                                                                                                                               |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `start()`                            | 启动前清理 Chrome 锁文件（`SingletonLock` 等）；`launch_persistent_context` 带 `accept_downloads=True`、`downloads_path` 指向 downloads 根目录；失败自动清理锁文件并重试，最多 3 次。 |
| `close()` / `__enter__` / `__exit__` | 关闭 context 与 playwright 实例，支持上下文管理器用法。                                                                                                           |
| `_cleanup_lock_files()`              | 删除 profile 目录中的 `Singleton*/lockfile/*.lock` 残留文件，防止 Chrome 启动失败。                                                                                |

#### 导航与元素操作

| 方法                                    | 说明                                                                                            |
| ------------------------------------- | --------------------------------------------------------------------------------------------- |
| `navigate(url, retries=3)`            | `goto` 带 3 次重试（`domcontentloaded`，60s 超时）。                                                    |
| `safe_click(selector, desc, retries)` | 等待选择器出现后点击，带重试。                                                                               |
| `safe_fill(selector, value, desc)`    | 等待选择器后 `fill`。                                                                                |
| `sleep(seconds)`                      | 固定等待（页面加载/动画）。                                                                                |
| `click_text(text, exact, retries)`    | 点击包含指定文字的按钮/链接（`get_by_text`）。                                                                |
| `click_selector(selector)`            | 点击 CSS 选择器元素。                                                                                 |
| `fill_placeholder(ph, value)`         | 按 placeholder 填输入框。                                                                           |
| `fill_selector(sel, value)`           | 按 CSS 选择器填输入框。                                                                                |
| `is_visible_text(text, timeout)`      | 页面是否出现指定文字（`wait_for`）。                                                                       |
| `close_popup(retries)`                | 按优先级关闭广告/引导弹窗：弹窗容器关闭按钮（zent/element-ui/antd 等 8 种）→ 通用关闭图标 → 文字按钮（"我知道了/跳过/关闭"）；无弹窗自动跳过，不误操作。 |

#### 下载捕获与文件归档（核心机制）

| 方法                                                   | 说明                                                                                                                        |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `set_export_context(platform, start, end, merchant)` | 设置导出上下文；任务 ID = `开始日期_结束日期`，保证同平台/商户/区间落同一文件夹。                                                                            |
| `begin_wait_download()`                              | **开启捕获模式**（点击"下载"前调用）：清空事件队列 → 记录捕获起点时间 → 归位根目录残留 UUID 文件 → 清空并重建 `下载目录/平台/商户/日期/临时` 目录。                                  |
| `end_wait_download()`                                | 结束捕获：清队列、关开关、归位根目录残留、清理临时目录（空目录 rmdir，带 8 次重试防瞬时文件锁）。                                                                     |
| `_on_download(download)`                             | 浏览器下载事件回调：捕获模式下把 `{dl, name}` 推入队列，否则忽略。                                                                                  |
| `wait_download(timeout=120)`                         | **等待下载完成**：优先消费事件队列（`_save_download_to_temp` 保存到临时目录 → `_finalize_download` 归档）；兜底轮询根目录新出现的稳定文件；超时返回 None 并清理残留。          |
| `_finalize_download(path)`                           | 将文件归入 `平台/商户/日期范围/`：顶层始终保留本次最新文件，同区间已存在同名旧文件则先复制到 `历史/原名_时间戳.扩展名` 再替换；移动失败有 10 次重试 + 拷贝兜底。                                |
| `_normalize_download_name(...)`                      | 按设置生成文件名：`unified`=统一命名 `平台_商户_日期区间.扩展名`；`original`=保留原始名+商户前缀（UUID/无名文件回退统一命名）。扩展名白名单 `.xlsx/.xls/.csv/.txt`，默认 `.xlsx`。 |
| `_carry_over_orphans()`                              | 把下载根目录残留的 UUID 无扩展名文件归位（`_finalize_download`）。                                                                            |
| `_cleanup_stale_files(root)`                         | 清理 `.~`/`.crdownload/.tmp/.part` 半成品残留。                                                                                   |

#### 信息与辅助

| 方法                    | 说明                                        |
| --------------------- | ----------------------------------------- |
| `screenshot(name)`    | 截图保存到 downloads 根目录（`名字_时间戳.png`），用于失败排查。 |
| `get_page_info()`     | 返回当前页 `{url, title}`，用于登录态判断。             |
| `latest_export_dir()` | 返回当前任务归档文件夹路径。                            |
| `_safe_name(name)`    | 静态方法：清洗路径非法字符，防目录逃逸。                      |

#### 有赞专项（位于 `platforms/youzan/export.py`）

有赞平台独有的逻辑（带日期参数的流水页 URL 构造）直接实现在 `YouzanExporter` 类内，不放入 `core/` 通用模块：

| 方法 | 说明 |
|------|------|
| `_build_youzan_url(url, start, end)` | 构造带 `startTime/endTime`（UTC+8 毫秒时间戳）与 `dateType=SETTLE_TIME` 的有赞流水页 URL，不执行跳转。 |
| `open_youzan_record(browser, url, start, end)` | 一次性打开已带日期参数的流水页（仅导航一次，避免二次跳转）。 |

> 早期版本曾将有赞 zent 日期选择器操作（`fill_zent_*` 等）提取为独立模块，但当前 URL 参数方案已无需操作页面日期组件，相关未使用的方法已删除。

### 5.3 `PlatformBase`（core/platform\_base.py）— 平台基类

全平台脚本必须继承的抽象基类，接口约定如下：

| 成员                                      | 类型  | 说明                                                                                            |
| --------------------------------------- | --- | --------------------------------------------------------------------------------------------- |
| `key`                                   | 类属性 | 唯一标识（与文件夹名一致，如 `"youzan"`），加载器以此为索引。                                                          |
| `name`                                  | 类属性 | 界面显示名（如 `"有赞"`）。                                                                              |
| `login_url` / `export_url`              | 类属性 | 登录页 / 流水导出页地址。                                                                                |
| `guide`                                 | 类属性 | 给用户看的操作指引文字。                                                                                  |
| `enabled`                               | 类属性 | 是否在界面中显示（默认 True）。                                                                            |
| `login(browser)`                        | 方法  | 默认实现：`navigate(login_url)` 等待用户手动登录；可覆盖做特殊处理。                                                 |
| `export(browser, start_date, end_date)` | 方法  | **核心接口**。返回 `"success"`（成功）/ `"manual"`（需手动）/ `"failed"`（失败）。默认实现：懒加载 `SmartExporter` 执行智能导出。 |

### 5.4 `SmartExporter`（core/exporters.py）— 智能导出器

当平台不自定义 `export()` 时由基类调用，自动执行通用导出流程：

| 方法                                  | 说明                                                                                                                        |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `_find_date_inputs()`               | 遍历页面 input，按 placeholder 关键词（开始/起始/start… 与 结束/截止/end…）识别起止日期框。                                                           |
| `_find_button(texts, exact)`        | 按文本匹配按钮，返回第一个命中的 locator。                                                                                                 |
| `_fill_dates(start, end)`           | 点击并填入日期，返回成功填写数量。                                                                                                         |
| `_click_query()`                    | 依次尝试「查询/搜索/确定/筛选/查找」。                                                                                                     |
| `_click_export()`                   | 依次尝试「导出/下载/生成报表/导出报表/下载账单…」。                                                                                              |
| `_click_confirm()`                  | 点击弹窗确认按钮（确定/确认/下载/导出/保存）。                                                                                                 |
| `_setup_dialog_handler()`           | 自动 `accept` 所有 JS 弹窗。                                                                                                     |
| `export(start, end, timeout)`       | 主流程：填日期 → 点查询 → `begin_wait_download` → 点导出 → `wait_download`；未捕获到下载则尝试确认弹窗再等一次；仍失败则截图 `manual_<时间戳>.png` 并返回 `"manual"`。 |
| `quick_export(start, end, timeout)` | 快速导出：不填日期，直接点导出（用于页面已有默认日期的场景）。                                                                                           |

### 5.5 KeepAliveService（core/keepalive.py）— 登录保活服务

| 方法/属性 | 说明 |
|-----------|------|
| `start()` | 启动后台守护线程，按间隔周期巡检（`stop` 事件驱动退出）。 |
| `stop()` | 置停止标志并等待线程退出（窗口关闭时由主程序调用）。 |
| `run_once()` | 巡检一轮：遍历 `app.iter_selected_merchants()`，逐个用独立 `BrowserManager` 打开 `login_url` 刷新会话，URL/标题含 login 或「登录」即标红（`app.set_platform_status(key,"error")`），否则标绿。 |
| `enabled` / `interval_min` | 可运行时调整；间隔改动即时生效。 |

> 并发约定：巡检轮询时若 `app.running == True`（正在导出/调试）立即跳过本轮，绝不与任务并发；`make_browser` 工厂注入便于单元测试。

### 5.6 模块级关键函数

| 函数                                    | 位置                 | 说明                                                                                                           |
| ------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `discover_platforms()`                | `core/loader.py`   | 扫描 `platforms/` 下含 `export.py` 的文件夹（跳过 `_` 开头的目录），动态导入，找出继承 `PlatformBase` 且定义 `key` 的子类并实例化，返回 `{key: 实例}`。 |
| `discover_merchants(platform_keys)`   | `core/main_gui.py` | 扫描 `browser_data/<平台key>/` 下的子目录，发现已建档商户，返回 `{key: [商户名]}`。                                                  |
| `load_settings()` / `save_settings()` | `core/config.py`   | 读写 `settings.json`，缺失字段回退 `DEFAULT_SETTINGS`（`show_browser`、`download_name_mode`）。                           |
| `log(message, level, callback)`       | `core/logger.py`   | 统一日志：输出控制台（容错 `pythonw` 无 stdout 场景）→ 写入 `logs/run_YYYYMMDD.log` → 转发 GUI 回调。                                |
| `check_dependencies()` / `main()`     | `core/main_gui.py` | 依赖检测与自动安装；程序入口与全局异常兜底。                                                                                       |

***

## 6. 依赖关系

### 6.1 模块依赖图

```
                     ┌──────────────┐
                     │ start.bat /  │
                     │ 启动工具.vbs   │
                     └──────┬───────┘
                            │ 启动
                            ▼
                     ┌──────────────┐
         ┌──────────►│core/main_gui │◄───────────┐
         │           │    .py       │            │
         │           └──┬──┬──┬────┘            │
         │              │  │  │                 │
         ▼              ▼  │  ▼                 │
 ┌─────────────┐  ┌────────┐  ┌──────────────┐  │
 │core/config  │  │core/   │  │core/loader   │  │
 │    .py      │  │logger  │  │ .py          │  │
 │(常量/设置)   │  │.py     │  └──────┬───────┘  │
 └─────────────┘  └────────┘         │          │
                                     ▼          │
         ┌───────────────────┐  ┌───────────────────────┐
         │core/browser.py    │  │core/platform_base.py  │
         │BrowserManager     │  │PlatformBase           │
         │(依赖 config,      │  └──────┬────────────────┘
         │ logger)           │         │ 继承
         └─────────┬─────────┘         ▼
                   │ 调用    ┌──────────────────────┐
                   │         │ platforms/*/export.py│
                   │         │ (平台插件)               │
                   │         └──────────────────────┘
                   │ 默认导出实现(懒加载)
                   ▼
         ┌─────────────────┐
         │core/exporters.py│
         │ SmartExporter   │
         │ (依赖 browser)   │
         └─────────────────┘
```

### 6.2 依赖明细

| 依赖方                     | 被依赖方                           | 说明                                                                      |
| ----------------------- | ------------------------------ | ----------------------------------------------------------------------- |
| `core/main_gui.py`      | `core.config`                  | 导入 `DOWNLOAD_DIR/BROWSER_DATA_DIR/ROOT_DIR/load_settings/save_settings` |
| `core/main_gui.py`      | `core.loader`                  | `discover_platforms()`                                                  |
| `core/main_gui.py`      | `core.logger`                  | `log()`，日志回调兼作 GUI 日志源                                                  |
| `core/main_gui.py`      | `core.browser.BrowserManager`  | 延迟导入（方法内），管理浏览器实例                                                       |
| `core/browser.py`       | `core.config`                  | `BROWSER_DATA_DIR/DOWNLOAD_DIR`                                         |
| `core/browser.py`       | `core.logger`                  | `log()`                                                                 |
| `core/loader.py`        | `core.platform_base`           | `PlatformBase` 判定                                                       |
| `core/platform_base.py` | `core.exporters.SmartExporter` | 方法内懒加载（默认导出实现）                                                          |
| `core/exporters.py`     | `browser`（鸭子类型）                | 使用 `begin_wait_download/wait_download/screenshot` 等                     |
| `platforms/youzan/export.py` | 标准库 | `datetime/urllib.parse`（`_build_youzan_url` 构造带日期参数的流水页 URL） |
| `platforms/*/export.py` | `core.platform_base`           | `from core.platform_base import PlatformBase`                           |
| `core/logger.py`        | 标准库                            | `logging`                                                               |
| 全部                      | Playwright                     | 外部依赖 `playwright==1.62.0`（`requirements.txt`）                           |

### 6.3 外部依赖

- **Python 3.10+**（启动脚本探测 Python3.10\~3.14 常见路径）

- **playwright==1.62.0** + Chromium 浏览器（首次运行由 `启动工具.vbs` 或 `check_dependencies()` 自动安装）

***

## 7. 核心流程解析

### 7.1 程序启动流程

```
启动脚本(start.bat / 启动工具.vbs)
  → 探测 Python 解释器
  → (vbs 专用)检查 playwright，缺失则自动安装
  → 运行 python -m core.main_gui
      ├─ check_dependencies()         # 兜底依赖检测
      ├─ LiushuiApp.__init__
      │    ├─ discover_platforms()    # 加载已实现平台插件
      │    ├─ load_settings()         # 读取 settings.json
      │    ├─ discover_merchants()    # 扫描已建档商户
      │    └─ 构建三栏界面
      └─ root.mainloop()              # 进入事件循环
```

### 7.2 首次登录流程（`_do_login`）

```
遍历 选中平台 × 勾选商户:
  _ensure_browser(plat, merchant, force_visible=True)   # 独立 profile，强制可见
  plat.login(browser)                                    # 打开登录页
  主线程弹窗「请在浏览器中完成登录」→ 后台线程阻塞 wait(600s)
  用户完成登录后点「确定」→ _login_confirm.set()
  关闭浏览器，进入下一个商户
登录状态按 browser_data/平台key/商户/ 目录持久化
```

### 7.3 导出流水流程（`_do_export`）

```
遍历 选中平台 × 勾选商户:
  _ensure_browser(plat, merchant)                        # headless 取决于"显示浏览器"设置
  browser.set_export_context(平台名, 起, 止, 商户)        # 下载归档上下文
  result = plat.export(browser, start_date, end_date)    # 平台插件逻辑
  └─ 平台自定义实现 / 或 PlatformBase 默认走 SmartExporter
       ├─ 填日期 → 点查询 → begin_wait_download → 点导出
       └─ wait_download: 事件队列优先 + 目录轮询兜底
  result: "success"→绿 | "manual"→橙(弹窗指引手动) | 其他→红
结算统计 → _copy_export_outputs 汇总文件 → 自动打开汇总文件夹
```

### 7.4 下载捕获与文件归档时序

```
点击"下载"前:  begin_wait_download()
               ├─ 清空事件队列, 打开捕获开关
               ├─ _carry_over_orphans()  # 归位上次残留 UUID 文件
               └─ 重建 <平台/商户/日期>/临时 目录
点击"下载"后:  context "download" 事件 → _on_download → 入队
wait_download(): 出队 → _save_download_to_temp(写入临时目录)
                 → _finalize_download(归档到 平台/商户/日期/)
                     ├─ 顶层同名旧文件 → 复制到 历史/原名_时间戳.ext
                     └─ os.replace 移动(10 次重试 + 拷贝兜底)
兜底: 轮询 downloads 根目录新出现的稳定文件(两份采样大小一致)
end_wait_download(): 清队列、归位残留、清理临时目录
```

### 7.5 登录态检查流程（`_do_check`）

```
遍历任务: _ensure_browser → navigate(login_url) → sleep(3)
  get_page_info() → URL 含 "login" 或标题含 "登录" → 未登录(红)
                     否则 → 已登录(绿)，显示页面标题前 20 字符
```

***

## 8. 平台插件体系

### 8.1 开发模型

每个平台 = `platforms/<key>/` 文件夹下的 `export.py`，定义一个继承 `PlatformBase` 的类并实现 `export()`。加载器按 `key` 注册，GUI 自动展示。详细编写规范见《脚本编写指南.md》，要点：

- `export()` 必须返回 `"success" / "manual" / "failed"` 三选一；

- 优先使用 `BrowserManager` 辅助方法（表格见 5.2）；

- 通用导出场景可直接不写 `export()`，走默认 `SmartExporter`；

- 复杂场景（iframe、特殊日期组件如 zent）可参考《脚本编写指南.md》中关于 iframe 切换的写法，以及有赞（URL 参数日期 + zent 日历）的实现。

### 8.2 已实现平台明细

**当前仓库仅实现 1 个平台：**

| key | 名称 | 登录/导出地址域 | 亮点实现 |
|-----|------|----------------|----------|
| `youzan` | 有赞 | youzan.com | **最完整示例**：URL 带日期参数直开流水页、`close_popup` 弹窗处理、zent 日期选择器、下载前 `begin_wait_download`、确认弹窗兜底 |

> 其余平台（微信支付、拼多多、抖音/抖店、小红书、京东、快手、视频号、银联、天猫/淘宝）为**待实现的开发模板**，用户已从 `platforms/` 移除；后续按 [8.1 开发模型](#81-开发模型) 补充各自的 `export.py` 即可自动生效。

所有平台脚本统一遵循：`navigate → sleep → fill 日期 → 查询 → begin_wait_download → 点导出 → 确认弹窗 → wait_download → 返回状态` 模板。

***

## 9. 项目运行方式

### 9.1 环境要求

| 依赖       | 要求                                     |
| -------- | -------------------------------------- |
| 操作系统     | Windows                                |
| Python   | 3.10+（启动脚本探测 Python 3.10\~3.14 常见安装路径） |
| Python 包 | `playwright==1.62.0` + Chromium 内核     |

### 9.2 启动方式（三选一）

| 方式       | 命令/操作                              | 特点                                                |
| -------- | ---------------------------------- | ------------------------------------------------- |
| 推荐（免控制台） | 双击 `启动工具.vbs`                      | 用 `pythonw` 运行无黑窗；首次自动检测并安装 playwright + Chromium |
| 命令行      | 双击 `start.bat`                     | 控制台常驻，错误信息直接可见，适合排查问题                             |
| 手动       | 在项目根目录执行 `python -m core.main_gui` | 需自行确保依赖已安装                                        |

手动安装依赖：

```bat
pip install playwright==1.62.0
python -m playwright install chromium
```

> 注：`core/browser.py` 检测到 `C:\pw_browsers` 目录存在时会设置 `PLAYWRIGHT_BROWSERS_PATH`，即支持自定义浏览器内核安装目录。

### 9.3 典型使用路径

1. 双击 `启动工具.vbs` 启动；
2. 左侧勾选平台，点平台右侧 `+` 添加商户（生成登录态目录）；
3. 勾选商户 → 点「首次登录」→ 浏览器中完成各平台登录；
4. 选择日期范围（或点「昨天/近7天/近30天」）→ 点「开始导出」→ 确认任务；
5. 完成后自动打开 `downloads/起_止_时间戳/` 汇总文件夹。

### 9.4 二次开发入口

- **新增/修改平台**：`platforms/<key>/export.py`，保存重启即生效；

- **修改平台 URL**：编辑对应平台类的 `export_url/login_url`；

- **扩展浏览器能力**：在 `BrowserManager` 增加辅助方法供全平台复用；

- **调整默认导出策略**：修改 `SmartExporter` 或 `PlatformBase.export` 默认实现。

***

## 10. 数据目录与文件约定

| 目录/文件                            | 自动创建           | 约定                                                                                     |
| -------------------------------- | -------------- | -------------------------------------------------------------------------------------- |
| `downloads/`                     | 是              | 导出文件根目录；运行时还有根目录临时落盘（UUID 文件名，随后被归位）                                                   |
| `downloads/<平台名>/<商户>/<起_止>/`    | 是              | 导出文件正式归档位置（无商户时省略商户层）                                                                  |
| `downloads/<平台名>/<商户>/<起_止>/历史/` | 是              | 同区间同名旧文件归档（`原名_时间戳.ext`）                                                               |
| `downloads/<平台名>/<商户>/<起_止>/临时/` | 是              | 单次任务下载临时暂存，结束后清空                                                                       |
| `downloads/<起_止>_<时间戳>/`         | 是              | 导出完成后全平台汇总目录（`_copy_export_outputs` 生成）                                                |
| `browser_data/<平台key>/<商户>/`     | 是（点 `+` 添加商户时） | Chromium 持久化 profile，保存登录态                                                             |
| `logs/run_YYYYMMDD.log`          | 是              | 当日统一日志（系统+操作，DEBUG 级）                                                                        |
| `settings.json`                  | 是              | 用户设置：`show_browser`（显示/隐藏浏览器）、`download_name_mode`（`unified` 统一命名 / `original` 保留原文件名）、`enable_keepalive`（登录保活开关）、`keepalive_interval_min`（保活间隔分钟） |

***

## 11. 附录：文件清单

| 文件                       | 规模（约）         | 作用                              |
| ------------------------ | ------------- | ------------------------------- |
| `core/main_gui.py`       | 860 行         | GUI 主程序与三大业务流程                  |
| `core/browser.py`        | 676 行         | 浏览器管理与下载归档（项目体量最大的核心模块）         |
| `core/exporters.py`      | 190 行         | 智能导出器（默认兜底导出）                   |
| `core/platform_base.py`  | 70 行          | 平台基类                            |
| `core/loader.py`         | 50 行          | 平台加载器                           |
| `core/config.py`         | 47 行          | 配置与设置读写                         |
| `core/logger.py`         | 38 行          | 日志模块                            |
| `core/keepalive.py` | 约 100 行 | 登录保活服务（后台线程周期巡检） |
| `tests/` | — | pytest 测试（test_logger、test_keepalive） |
| `requirements-dev.txt` | — | 开发依赖（pytest） |
| `platforms/*/export.py` | 约 110 行 | 平台导出脚本（**当前仅 `youzan` 已实现**，含有赞专项的 URL 日期参数方法；其余平台为待实现模板） |
| `start.bat` / `启动工具.vbs` | —             | 启动脚本（`python -m core.main_gui`） |
| `使用说明.md` / `脚本编写指南.md`  | —             | 用户文档 / 开发文档                     |

***

*文档生成基于当前仓库代码（无 git 提交历史，为首版代码）。如代码更新，请同步维护本文档。*
