# 天气（设备定位）与钉钉只读听记 · 补丁说明（含部署/回滚）

适用：已安装的 Jarvis **2.0.4**（macOS）。中文语音和“有波形但不回应”的排查见 [chinese-voice.md](chinese-voice.md)，同一个补丁包。本补丁基于官方 `v2.0.4` 标签（与官方 arm64 安装包里的 `jarvis/ hud/ app/` 逐文件一致，已核对），
只新增或替换文件，不改上游、不改 `VERSION`、不改 `app/models.json`、不动语音模型、不动 `config.json` 里已有的 DeepSeek 配置、语音和记忆。

> 本文只描述行为和做法，不含任何真实城市、坐标、公司内容、账号或密钥。示例里的路径、城市都是占位。

## 1. 改了什么

| 项 | 之前 | 现在 |
|---|---|---|
| HUD 天气 | 写死“上海 18°、AQI 36、14°/22°…” | 只显示真实数据；没有数据显示“天气不可用”和原因 |
| HUD 路线 | 写死“去 张江科学城 32 分钟 24 km”和示例路线图 | 没有路线时显示“暂无路线”，不画示例路线 |
| HUD 中心坐标 | 写死上海经纬度 | 不显示 |
| 位置来源 | 高德按 **IP** 猜城市 | **设备定位**（系统定位服务，用户授权）；拒绝/不可用时由用户**选城市**；不再用 IP 或时区当位置 |
| 天气数据源 | 高德天气（要用户自己的 key） | [Open-Meteo](https://open-meteo.com)：公开文档、免费（非商业）、不要账号、不要 key |
| 钉钉 | 无 | 只读：Jarvis 问“最新一条听记”→ 标题、时间、来源链接 |

HUD 无语音服务时（浏览器直接打开）也不再回答“上海今天多云 18 度”，而是说没有数据。

### 天气面板怎么显示

- 有数据：温度、天气、城市名（设备定位时是系统给出的地名）、湿度、**US AQI**（模型估算，取不到就显示“AQI 不可用”）、未来三天。
- 底下一行始终写明：`来源 Open-Meteo · 设备定位/手动选择城市 · 更新 HH:MM（x 分钟前）`；刷新失败但还有 3 小时内的旧数据时，加“刷新失败，沿用旧数据”。
- 观测时间超过 **3 小时**、或服务返回无法识别的数据、或网络失败又没有 3 小时内的旧数据：显示“天气不可用”和原因，**不继续显示旧数字**。HUD 自己也每分钟检查一次，服务停止更新超过 3 小时同样转为不可用。
- 右上角齿轮：「使用设备定位」按钮；搜索城市（Open-Meteo 地名搜索，中英文都行）并点选。选城市后记在 `~/.jarvis/config.json` 的 `weather` 段（只存城市名和城市中心坐标）。

### 位置规则

1. `weather.mode = "device"`（默认）：向 macOS 定位服务要一次位置；用户拒绝、系统定位关闭、被限制、超时、没有编译工具……一律显示不可用并给出原因，**不会**偷偷换成 IP/时区/默认城市。
2. `weather.mode = "city"`：只用用户选的城市，不请求定位。
3. 设备坐标只留在内存里，不写盘、不写日志；发给 Open-Meteo 前四舍五入到 0.01°（约 1 公里）。
4. 高德插件现在只管路线：没有保存“家”就没有起点（不再用 IP 估计），会提示先说家的地址。「地球此刻」的“家”依次取 `earth.home` → 已保存的家 → 天气位置 → 原有的“北京（默认）”标记。

语音问天气：工具 `get_weather` 现在由天气插件提供（高德插件不再提供同名工具）。没有数据时会告诉模型“不可用，不要编数字”。

## 2. macOS 定位：调查结论（重要）

- 官方安装包里的 Python（python-build-standalone 3.12）是**裸可执行文件**：没有 `CFBundleIdentifier`，也没有任何 `NSLocation…UsageDescription`（已在官方 dmg 里核对）。macOS 的定位授权按 App（Bundle ID + Info.plist 里的用途说明）发放，pyobjc 作者也明确说过需要 App Bundle。所以在 Jarvis 的 Python 进程里直接调 CoreLocation **不可靠**。
- `pywebview` 的 WKWebView 里用 `navigator.geolocation` 同理要走宿主进程的定位授权，宿主还是这个裸 Python，所以也不用它。
- 采用的做法：`jarvis/platforms/maclocate_helper/main.swift`（几十行）+ `Info.plist`，第一次需要时用 Xcode 命令行工具编译成 `~/Library/Application Support/Jarvis/JarvisLocate.app`（临时签名），之后每次用 `open -W -n -a` 启动它取一次位置，结果写成临时 JSON 再删除。macOS 会弹出标准的“Jarvis Location 想使用你的位置”，授权在「系统设置 → 隐私与安全性 → 定位服务」里能看到、能关。
- 前提：Mac 装了 Xcode 命令行工具（`xcode-select -p` 成功）。没装时程序**不会**调用 `swiftc`（否则系统会弹安装窗口），而是显示“需要 Xcode 命令行工具”，你仍可选城市。

**没有在 Mac 上验证过**：云端是 Linux，只能验证 Python 一侧（假的 helper）和 Swift 源码的静态检查。Swift 代码没有在云端编译。以下必须你在 Mac 上验收（见第 6 节）：helper 能否编译、授权弹窗是否出现、授权后能否拿到位置、拒绝后的提示、ad-hoc 签名的 helper 在你的系统版本上是否能拿到定位。若这条路在你的系统上不通，天气会明确显示“不可用”并让你选城市，不会造数据。

## 3. 钉钉只读（dws）

依据你提供的 dws v1.0.57 帮助归档（`dws minutes` 相关 help 和 `dws schema minutes --compact`）实现，**没有猜命令或参数**：

| 用途 | 命令（固定写死，参数里只有两处变量） | 依据 |
|---|---|---|
| 列最新听记 | `dws minutes list mine\|all --limit 10 --format json --timeout N` | `list mine --help`（`--limit`）；`list all` 的 `--limit` 见 schema 描述；`--format`、`--timeout` 是全局参数 |
| 取基础信息 | `dws minutes get info --id <taskUuid> --format json --timeout N` | `get info --help`：返回创建人、开始/截止时间、标题、访问链接 |

- 范围变量 `scope` 只能是 `mine`（默认，我创建的）或 `all`（我有权限的全部）。`taskUuid` 来自 dws 自己的列表输出，必须匹配 `^[A-Za-z0-9][A-Za-z0-9_.=+/-]{3,127}$` 且不含 `..`，否则不使用。
- 只返回：标题、时间、来源链接（只接受 http/https，不含账号密码）。**不拉**逐字稿、摘要、待办、参与人；创建人姓名也不往下传。
- 和其他工具一样，工具结果（这里是标题、时间、链接；天气是城市名和天气）会作为文字交给你自己选的大模型去组织回答，也会写进本机的记忆日志 `~/.jarvis/memory/`。不想让某条听记标题经过大模型就不要问它。
- HUD 左下角出现一张小卡片（标题、时间、链接、“复制链接”），语音里让 Jarvis 说标题和时间。
- 白名单在 `jarvis/plugins/dingtalk.py` 的 `check_argv`：命令必须**恰好**是上面两种形状，否则在启动进程之前就拒绝。发消息、审批、删除、更新、上传、录音、加权限、`--yes`、`--mock`、`--dry-run`、`--jq`、任意 shell 全部不可能走到。不经过 shell，参数是列表。
- 复用你本机 dws 的登录：不读、不复制 token/cookie，也不要你的 DeepSeek key。子进程只拿到一小撮环境变量（`HOME`、`PATH`、代理、`DWS_*` 等），拿不到大模型 key。
- dws 路径是**本机配置**，不在仓库里：`~/.jarvis/config.json`

  ```json
  "dingtalk": {"dws_path": "<你的 dws 绝对路径>", "timeout": 20}
  ```

  或环境变量 `JARVIS_DWS_PATH`。没配置时这个工具根本不提供给大模型。`install.sh --dws <路径>` 会帮你写进去。
- 错误都有明确说法：未配置、找不到/不可执行、未登录或授权过期（提示用 `dws auth --help` 查登录方式，不猜命令）、超时（杀掉整个进程组）、返回不是 JSON/过大/看不懂（只回报**键名**，不回报内容）、没有听记。
- **不确定点**：dws 的 JSON 字段名不在 help 里。读取器按文档描述找 `taskUuid`、标题、创建/开始时间、链接，并容忍常见写法；若真实输出是别的结构，会返回 `bad_output` 和键名，把键名发给我即可调整，不会乱猜内容。
- 回滚/禁用：`~/.jarvis/config.json` 里 `"plugins": {"dingtalk": false}`，或删掉 `dingtalk.dws_path`。

## 4. 全屏快捷键 Ctrl+Cmd+F

只做了代码核查，**未能在 Mac 上实测**，没有发现明确 bug，所以没有改动：

- pywebview 5.4 在 macOS 上会建一个“View → Enter Full Screen”菜单，快捷键就是 ⌃⌘F（`toggleFullScreen:`），在首次显示窗口时加入。
- HUD 的所有键盘处理里，没有哪一个会拦截 Ctrl+Cmd+F（凡是带 Ctrl/Cmd 的，要么直接 return，要么只处理 Ctrl+Shift 或 Ctrl/Cmd+B、K）。
- 唯一不确定的是窗口的 collection behavior：pywebview 只在它自己的 `toggle_fullscreen()` 里设置全屏标志，创建窗口时不设；是否因此让菜单项失灵取决于系统版本。验收时按 ⌃⌘F 试一下；如果不行，告诉我，最小修复就是窗口显示后设一次 `NSWindowCollectionBehaviorFullScreenPrimary`（约 5 行，仅 macOS）。

## 5. 部署与回滚（针对已安装的 2.0.4）

补丁包：`jarvis-2.0.4-zh-weather-dws-patch.tar.gz`（含 `install.sh`、`rollback.sh`、`manifest.txt`、`files/`），有 `.sha256`。

```bash
tar xzf jarvis-2.0.4-zh-weather-dws-patch.tar.gz && cd jarvis-2.0.4-zh-weather-dws-patch
shasum -a 256 -c ../jarvis-2.0.4-zh-weather-dws-patch.tar.gz.sha256      # 解包前先在压缩包所在目录校验
./install.sh --dry-run                                                # 只检查，不改动
./install.sh --dws "<你的 dws 绝对路径>"                              # 先退出 Jarvis；不要钉钉可以不带 --dws
```

- 脚本先核对：是 macOS、App 版本是 2.0.4、要替换的每个文件的 sha256 都等于官方 2.0.4 原文件（不是就停下，`--force` 才继续）、Jarvis 没在运行。
- 备份原文件到 `~/Library/Application Support/Jarvis/patch-backup/<时间>/`，复制后逐个校验 sha256。
- **不重新签名**（默认）：App 的签名身份不变，已经授予的麦克风、摄像头权限继续有效。如果 macOS 提示“已损坏”，加 `--resign` 重新做 ad-hoc 签名，之后麦克风权限可能需要重新允许（必要时 `tccutil reset Microphone io.github.shitoudaidi.jarvis`）。
- 预先编译定位 helper；没有 Xcode 命令行工具时会明说，天气仍可手动选城市。
- 如果终端没有写 `/Applications/Jarvis.app` 的权限（macOS 的“App 管理”保护），脚本会报错并停止，给终端授权后重试。
- 回滚：`./rollback.sh`（还原备份、删除新增文件和编译出的 helper；`--clean-config` 同时还原 `dingtalk` 配置并删掉 `weather` 配置）。语音模型、DeepSeek 配置、记忆都不在补丁范围内，不受影响。

从源码重建补丁包：`bash tools/mac-patch/make-patch.sh`。

## 6. Mac 验收清单（请在装好补丁的 Mac 上做）

天气：
1. 打开 Jarvis。天气面板先显示“正在定位”，系统弹出“Jarvis Location 想使用你的位置”；点允许。面板应显示真实温度、城市、`来源 Open-Meteo · 设备定位 · 更新 HH:MM`。
2. 在「系统设置 → 隐私与安全性 → 定位服务」里应能看到 Jarvis Location；把它关掉，点天气面板齿轮 →「使用设备定位」，应显示“定位权限被拒绝…”，**不是**旧数据或别的城市。
3. 齿轮里搜索一个城市并点选：显示该城市天气，来源写“手动选择城市”。
4. 断网后等下一次刷新（或点齿轮里的操作触发）：3 小时内显示旧数据并标“沿用旧数据”，超过 3 小时变“天气不可用”。
5. 问 “Hey Jarvis, what's the weather?”：回答与面板一致，没数据时说明不可用。
6. 如果 helper 没编译成功：面板应写“需要 Xcode 命令行工具…”，并且仍可选城市。把 `~/.jarvis/voice.log` 里 `location helper:` 开头的行（不含个人信息）发给我。

单独诊断定位（不打印坐标）：

```bash
/Applications/Jarvis.app/Contents/Resources/python/bin/python3 -c '
import sys; sys.path.insert(0, "/Applications/Jarvis.app/Contents/Resources")
from jarvis.platforms import maclocate
r = maclocate.locate()
print({k: v for k, v in r.items() if k not in ("lat", "lon", "name")})'
```

输出 `{'ok': True, ...}` 说明定位走通；否则 `reason` 就是原因（denied / no_toolchain / build_failed / helper_failed / timeout …）。

钉钉（先确认你终端里 dws 已登录）：
7. `./install.sh --dws "<路径>"` 之后重开 Jarvis，说“最新一条听记是什么”：应回答标题和时间，屏幕左下出现卡片，带来源链接和“复制链接”。
8. 把 dws 登出或改错路径再问：应说明未授权/找不到 dws，不编内容。
9. 如果回答 `bad_output`：记下返回里的键名（不含内容）发给我。

其它：
10. 语音唤醒、说话（jarvis-high）、记忆、DeepSeek 对话都和以前一样；`Ctrl+Cmd+F` 能否全屏。
11. 打开 `~/.jarvis/voice.log`，确认没有坐标；`ls ~/.jarvis/run/` 应为空（定位结果文件读完即删）。

## 7. 测试（云端，全是 mock，不等于端到端）

```bash
python tests/test_weather.py        # 天气：授权拒绝/不可用/网络失败/时效/选城市/AQI 缺失/无坐标泄露/不再用 IP；Mac 定位客户端（假 helper）
python tests/test_dingtalk.py       # 钉钉：未安装/未配置/未授权/超时/乱码/注入/写操作被拒/环境变量/合法只读结果（假 dws）
node tests/hud_weather_check.js http://127.0.0.1:8811/index.html   # HUD 面板：无演示数据、来源与时间、不可用、过期、设置框、钉钉卡片（先 cd hud && python3 -m http.server 8811）
```

这些测试证明程序逻辑，**不证明** Mac 上的 CoreLocation 授权流程、真实 dws 的 JSON 形状、真实网络上的 Open-Meteo。这几项靠第 6 节验收。

## 8. 与既有约束的关系

- DeepSeek llm 配置、语音（必需的 10 个模型）、记忆：未改动；补丁里没有 `app/models.json`、没有模型文件。备选声音（Alan）校验不符的问题不在本补丁范围，也不会被强装。
- DeepSeek Harness 是可选插件，本补丁不依赖它。
- 不使用付费服务、不需要注册；唯一的外部服务是 Open-Meteo（天气/空气质量/地名搜索）和用户本机已登录的 dws。
