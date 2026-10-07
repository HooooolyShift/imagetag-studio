# 技术文档 1 ｜ PC 端主程序与通用约定

> 负责人：本会话（PC 端主程序 / 总协调）
> **规矩**：本会话每次改动后，把"改了哪个文件、为什么、怎么验证"写进本文件末尾的《变更记录》；
> 其他 4 份技术文档（AI 生图 / 宣传片 / 平板端 / 手机端）随时可读，跨端要用的格式与约定以本文件为准。

## 一、两份程序（2026-10-07 起）

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

## 五、发布流程（正式版，照做不用问）

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

- 2026-10-07：建立本套 5 份技术文档；E/D 双副本 + 便携数据 + BETA 标识；测试图迁入 E 的 `测试图/`。
