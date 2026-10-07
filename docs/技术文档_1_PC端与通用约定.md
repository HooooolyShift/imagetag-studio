# 技术文档 1 ｜ PC 端主程序与通用约定

> 负责人：本会话（PC 端主程序 / 总协调）
> **规矩**：本会话每次改动后，把"改了哪个文件、为什么、怎么验证"写进本文件末尾的《变更记录》；
> 其他 4 份技术文档（AI 生图 / 宣传片 / 平板端 / 手机端）随时可读，跨端要用的格式与约定以本文件为准。

## ⏰ 断电纪律（每天 23:00 断电，用户 2026-10-07 告知）

- **22:30 起停止开新功能**，只做收尾：跑自检 → 本地 commit → 写清"做到哪 / 下一步"。
- 任何时刻断电都不能留下半成品：改到一半的代码必须能 import（先保证语法与主流程可用），
  宁可把功能拆成两批，也别让仓库处于跑不起来的状态。
- 长任务（下载模型、批量出图、打包、上传）要么 22:30 前收尾，要么拆成可续做的小批。
- 每晚 22:30 前各会话在**自己的技术文档**里写一行当天进度与下次起点。

## 一、两份程序（2026-10-07 起）

### 开发目录约定（2026-10-07 用户要求：统一用 BETA 目录开发）

| 端 | 唯一开发目录 | 说明 |
|---|---|---|
| **PC 端** | `E:\文档\ChatGPT\图片标签分类` | 这就是 BETA 版（有 `beta.flag`）；**所有 PC 端开发/测试都在这里做** |
| PC 端正式副本 | `D:\图片标签分类` | **只接收正式同步**，平时不要在里面改东西 |
| **平板端** | `E:\文档\ChatGPT\图片标签工坊_移动端` | 平板端工程（WebUI，dev-server 在 `tools\dev-server.cmd`）；**只有平板端会话动它** |
| **手机端** | `E:\文档\ChatGPT\图片标签工坊-手机端` | 手机端独立目录；**只有手机端会话动它**（当前只做浏览+图谱，暂缓） |

跨端共用的事实源：**tag 解析 / 分级 / 中文名映射 / 词表** 一律以 PC 仓库
`app\naming.py`、`app\tag_i18n.py`、`app\tag_zh_dict.json`、`app\tag_zh_corrections.json` 为准，
移动端各自实现或拷贝，**不要共享一个可变目录**（这就是"各自独立"的意思）。

| 位置 | 角色 | 图标 | 数据目录 |
|---|---|---|---|
| `E:\文档\ChatGPT\图片标签分类` | **测试版 / 开发版（BETA）** | `assets\icon_beta.ico`（带 BETA 角标） | 程序目录下 `data\`（便携模式，独立空库起步） |
| `D:\图片标签分类` | **正式版（稳定）** | `assets\icon.ico` | 程序目录下 `data\`（建立时从 `%LOCALAPPDATA%\ImageTagStudio` 快照而来） |
| `K:\ImageTagStudio` | 发布/安装包制作区 | — | 无 `data/`，安装版仍用 `%LOCALAPPDATA%\ImageTagStudio` |

- 桌面快捷方式：`图片标签工坊 Beta.lnk`（→ E）、`图片标签工坊 正式版.lnk`（→ D）。
- **新功能一律先在 E 的开发版做**；只有正式推送时才把更新同步到 D（`git -C D:\图片标签分类 pull --ff-only`）。
- 测试版标识机制：程序目录存在 `beta.flag` → `config.is_beta()` 为真 → 窗口/托盘图标换成 BETA 版、应用名带 "Beta"。
- 便携数据机制：程序目录存在 `data/` → `config.data_dir()` 用它；否则回落 `%LOCALAPPDATA%\ImageTagStudio`。
  因此正式版与测试版的库、设置、缩略图完全隔离，可以同时运行互不干扰。

## 二、怎么跑起来

### 缩略图缓存与词典（跨端必须一致，平板端 2026-10-07 核对过）

- **缩略图键**：`app/imaging.py` 的 `_thumb_key(file_id, mtime, size)`
  = `md5(f"{file_id}|{mtime:.3f}|{size}")`，**第三段是缩略图边长（不是文件字节数）**；
  落盘路径 = `<data_dir>\thumbs\<key 前 2 位>\<key>.jpg`。
- **各处用的边长**（改动 UI 前先看这里，别问错档）：

  | 场景 | 边长 | 出处 |
  |---|---|---|
  | 图库网格 | `max(320, thumb_size*2)` → 本机 `thumb_size=170` 时 **340** | `app/ui/main_window.py:434` |
  | 审核台队列 | **200** | `app/ui/review.py:50` |
  | 大图/预览对话框 | 160 | `app/ui/dialogs.py:1401` |
  | 导入对话框 | 320 | `app/ui/import_dialog.py:35` |
  | 其它通用池 | `size + 40` | `app/ui/common.py:106` |

  真机实测（11144 行）：340 命中 4897、320 命中 6，其余没生成过。
  **移动端建议探测顺序 340 → 320 → 200 → 160，只登记真实存在的文件**。
- **中文词典**：运行期只读 `app/tag_zh_dict.json`（现 128840 条，`app/tag_i18n.py:187` 加载）。
  `app/tag_zh_corrections.json`（247 条）**只是给 tools 脚本用的修正表**，已全并进 dict，运行期不读它。
- **标签分类**：读库里的 `categories` 表（列 `key/label/templates/sort/color/builtin/created_at`），
  现在有 19 条（character / real_person / clothing / count / pose / body / scene / style / action /
  series / other / rating / object / body_detail / text_ui / event / interaction / view / misc），
  **比内置默认的 12 条多**，两端都按库为准。

```powershell
# 开发/测试版（E）
cd "E:\文档\ChatGPT\图片标签分类"; C:\Users\27838\.conda\envs\imtag\pythonw.exe -m app.main
# 正式版（D）
cd "D:\图片标签分类";            C:\Users\27838\.conda\envs\imtag\pythonw.exe -m app.main
```

- Python 环境：`C:\Users\27838\.conda\envs\imtag\python.exe`（依赖已装齐，离线可用）
- 启动器配置：`launcher.ini`、`run.cmd`
- 测试图：`E:\文档\ChatGPT\图片标签分类\测试图\`（112 个文件，来自宣传片会话下载的图，已设为测试版默认扫描来源）

## 三、数据与数据库

- 数据库：`<data_dir>\library.db`（SQLite）。测试版在 `E:\...\data\library.db`，正式版在 `D:\...\data\library.db`。
- 缩略图缓存：`<data_dir>\thumbs\`；日志：`settings.log` / `blur.log` / `thumbs.log`。
- 原库快照（迁移时的安全底）：`D:\图片标签分类\data\`（由 `%LOCALAPPDATA%\ImageTagStudio` 复制，未删除原件）。
- **规则：任何改数据库的操作前先备份 library.db**；用户的真实图库 `K:\ImageTags`（图库）与 `K:\pictures`（来源）绝不能删。
- 模型学习反馈（人工确认/否决的 tag）都写在 library.db 里，属于"模型学习数据"，迁移/备份时必须一起带上。

## 四、目录结构（要点）

```
app/            主程序（config 配置与路径、store 数据库、library 业务、workers 后台任务、ui/ 界面）
assets/         图标与开屏图（splash/ = 14 张 1536x648 封面）
tools/          自检与打包脚本（uicheck.py、graphcheck.py、namingcheck.py、reviewtagcheck.py、
                reviewthumbcheck.py、tagfiltercheck.py、settingscheck.py、deadcodecheck.py、
                build_update.py、make_manual.py、split_release.py、gen_splash.py）
docs/           本套技术文档
models/         本地模型（约 3 GB）
测试图/          测试版用的素材（不进版本库）
```

## 五、发布流程（**只在用户明确说"正式更新"时才做**）

> **规矩（2026-10-07 起）**：改完、验证完**不要自己 push、不要自己更 release、不要自己同步 D 盘**。
> 把结果留在本地、汇报给用户；等用户说"推送 / 正式更新 / 发版"再执行下面这套。
> 日常的本地 commit 照常做（用来记录改动），只是不推。

1. 推代码：`git -c http.proxy=http://127.0.0.1:29758 push origin master`
2. 重建更新包：`python tools\build_update.py "K:\ImageTagStudio\更新包"` → 压成 `K:\ImageTagStudio\更新包.7z`
3. 重建完整包：同步 `K:\ImageTagStudio\安装包\payload` 后压成 `K:\ImageTagStudio\安装包.7z`
4. 切分卷：`python tools\split_release.py "K:\ImageTagStudio\安装包" "<输出目录>"`（GitHub 单文件 2 GB 上限）
5. 说明书有界面改动时：`python tools\make_manual.py`
6. 刷新 GitHub release `HooooolyShift/imagetag-studio` 的 v1.4 资产（更新包 / 说明书 / 公告 / 封面 / payload 分卷 / 合并脚本 / SHA256）
7. 同步正式版副本：`git -C "D:\图片标签分类" pull --ff-only origin master`

## 六、已知坑（踩过的，别再踩）

- 网格/树要拖拽：`Qt.ItemIsDragEnabled` + `setAcceptDrops(True)` + `DropDragMode` 少一个就"拖不动/禁止符号"。
- `reindex_after_move` 只改 path 不改 `root_id` → 拖进图库后看不到。
- `set_series_order` 不能走 `_update_path`（会被防重名改成 `001_2.jpg`），要直接写库。
- 保存设置：**不要**用旧 settings 对象整体覆盖 `settings.json`（曾把用户设置冲掉）；设置项走"改动即存"。
- 打包/切分脚本必须用**支持 UTF-8 的 pwsh** 运行（Windows PowerShell 5.1 读中文路径会乱码）。

## 七、变更记录

## 七·补　413（请求体过大）巡查约定

- 背景：Codex 每轮会把整条会话历史重发；历史里累积的 base64 截图会把请求体推到几十 MB，
  撞到服务端上限后**那条线程每一轮都会失败**（unexpected status 413 / Payload Too Large）。
- **巡查只由 PC 端主程序会话负责**（用户 2026-10-07 明确：所有会话各自查会乱套）：
  该会话挂了每日 21:00 的定时巡查（app automation，本线程），只在出现「危」时才提醒用户。
  **其他会话不要自己跑扫描、也不要做裁剪**；如果发现某条线程"发不出消息/一读图就报错"，
  把线索报给用户或 PC 端主程序会话即可。需要复核时，扫描命令是：

  ```powershell
  pwsh -NoProfile -File "$env:USERPROFILE\.codex\skills\codex-session-trim\scripts\scan-sessions.ps1" -IncludeArchived
  ```

  看本项目（cwd = `E:\文档\ChatGPT\图片标签分类`）的线程有没有被标「危」（请求体 ≥40 MiB，
  或 ≥20 MiB 且历史上真报过 413）。
- **裁剪必须先征得用户同意**：`trim-rollout-images.ps1 -Apply` 会把历史里的大图换成灰色占位
  （等长替换，不动偏移），脚本会先自动备份；裁完需要用户**完全退出 Codex 再打开**才生效。
- 日常减少风险的土办法：看过的对比图/截图尽量不反复贴；需要长期留存的图直接写文件
  （例如 `.splash_gen/`、`docs/`），别只留在对话里。

## 八、变更记录

- 2026-10-07：建立本套 5 份技术文档；E/D 双副本 + 便携数据 + BETA 标识；测试图迁入 E 的 `测试图/`。
- 2026-10-07：加入 413 巡查约定；1.5 范围补充 PC 端（浏览模式/右键菜单/多实例开屏/设备管理菜单/最小化托盘）
  与移动端（局域网、平板两档模式、手机端只做浏览+图谱）。
