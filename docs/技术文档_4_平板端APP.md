# 技术文档 4 ｜ 平板端 APP

> 负责人：平板端 APP 开发会话（新建，2026-10-07）
> **规矩**：每次做出决定（技术栈、目录、接口、数据库结构）或改动代码后，立刻写进本文件；
> 其他 4 份技术文档（PC 端 / AI 生图 / 宣传片 / 手机端）随时可读，**跨端共用的格式与约定以《技术文档 1》为准**。

## 一、目标（未开工，先把边界定清楚）

在 Android 平板上浏览 / 检索 PC 端「图片标签工坊」的图库：按 tag 检索、看缩略图与大图、
（可选）做审核与打标，并支持 MIX 等文件管理器能识别的文件名 tag 规则。
定位为**只读/轻编辑的伴生端**，重活（扫描、打标、模型训练）仍在 PC 端做。

## 二、数据交换格式草案 v0.1（2026-10-07，**待冻结**）

> 本节是"草案"，不是已生效约定。冻结前需要 PC 端主程序会话确认（见第六节待确认项）。
> 冻结后，字段级约定要同步进《技术文档 1》，手机端共用（手机端已有同样要求，见《技术文档 5》）。

### 2.1 交换产物：一个「索引快照包」

PC 端产出一个目录（或压成一个 zip），平板端整体导入：

```
ImageTags_Snapshot_20261007\
  manifest.json        格式版本、生成时间、来源、校验和（见 2.4）
  index.db             裁剪版 SQLite 索引（见 2.3），平板只读打开
  thumbs\              直接复用 PC 端已生成的缩略图目录（原样拷贝）
    ab\abcd....jpg
  images\              可选：如走"离线快照"方式，放精选/全量原图或分卷
```

### 2.2 为什么首选"DB 快照 + thumbs"而不是纯 JSON 索引

关键证据是**缩略图文件名算不出来**：PC 端缩略图路径规则在 `app/imaging.py:thumb_path()`——

```
key  = md5(f"{file_id}|{mtime:.3f}|{size}")          # 320px JPEG, quality=82
路径 = <data_dir>\thumbs\<key[:2]>\<key>.jpg
```

`file_id`、`mtime`、`size` 三个值里，`file_id` 只有数据库有。所以：

| 方案 | 平板拿到缩略图的代价 | 索引一致性风险 | 平板侧实现量 |
|---|---|---|---|
| **A. index.db（裁剪 DB）+ thumbs 目录**（推荐） | 零成本，按同一条公式定位 | 低（同一份数据源） | 小：只写"读表 + 查询" |
| B. JSON 索引 + thumbs 目录 | 必须由 PC 端额外导出 `thumb_key` 字段 | 中（两套导出逻辑要同步改） | 中：要自建索引缓存（Room/内存表） |
| C. JSON 索引 + 平板自生成缩略图 | 要能读到原图（LAN/SAF）并本地解码 | 高（缩略图规格两端不一致） | 大 |

结论：**方案 A 为首选**，B 作为 A 万一不可用时的备选（B 的 JSON 里必须带 `thumb` 字段）。
方案 C 只在"平板本地已有图库副本"时才考虑。

### 2.3 给平板的必须是「裁剪版」索引库，不是原库

原库（当前 `%LOCALAPPDATA%\ImageTagStudio\library.db` 约 21.9 MB）里塞了大量**模型学习数据**，
对平板毫无用处、还涉及体积与隐私：

- `files.clip_vec`（CLIP 特征 BLOB）、`faces.emb`（人脸底库）、`regions.clip_vec*`、
  `tag_probe.data`（自训练分类器）、`files.auto_json`（自动打标原始分数）。

裁剪版（`index.db`）**保留**：

| 表 | 保留字段 | 用途 |
|---|---|---|
| `meta` | 全部 | 加一个 `snapshot_format` 版本号 |
| `roots` | id, path, label, is_library, drive | 区分图库 / 来源 |
| `files` | id, root_id, path, rel, name, ext, size, mtime, width, height, series_id, page_no, kind, rating, manual, reviewed, missing | 列表、检索、缩略图定位 |
| `tags` | 全部（去掉 `prompt` 也行） | 名称、分类、中文名、计数 |
| `categories` | 全部 | **分类以库为准**，见 2.5-③ |
| `file_tags` | 全部 | 图 ↔ 标签（含 source / status / score） |
| `series` | 全部 | 系列名、标签、页数 |
| `nodes` / `taxonomy_edges` | 全部 | 标签体系、父子/平行关系 |
| `dup_feedback` | 全部 | 误报标记（可选） |

**去掉**：`clip_vec`、`clip_model`、`phash/phash2/ahash`、`auto_json`、`rating_scores`、`wd_done/clip_done/face_done`、
`faces`、`persons`、`regions`、`tag_probe`、`layout`、`jobs`。

估算：裁剪后体积约几百 KB ~ 2 MB 量级（对比：7688 张缩略图 = 117 MB，索引根本不是瓶颈）。

### 2.4 manifest.json 结构（草案）

```json
{
  "snapshot_format": 1,
  "generated_at": "2026-10-07T20:00:00+08:00",
  "source": { "app": "图片标签工坊", "version": "1.4.0", "edition": "正式版",
              "data_dir": "C:\\Users\\27838\\AppData\\Local\\ImageTagStudio" },
  "roots": [ { "id": 5, "path": "K:\\ImageTags", "is_library": true },
             { "id": 4, "path": "K:\\pictures",  "is_library": false } ],
  "counts": { "files": 8472, "tags": 4291, "tags_used": 467, "file_tags": 6496, "series": 1 },
  "thumb_spec": { "algo": "md5(file_id|mtime|size)", "dir": "thumbs", "ext": ".jpg", "size": 320, "quality": 82 },
  "dict": { "file": "app/tag_zh_dict.json", "sha1": "…", "entries": 128840 },
  "index": { "file": "index.db", "sha256": "…", "schema": "1" }
}
```

平板端启动时校验 `snapshot_format`（不认识就明确报错，不猜）+ `index.sha256`；
`thumb_spec` 定死，PC 端改缩略图规格就要抬 `snapshot_format`。

### 2.5 字段级约定（**两端必须完全一致**，冻结后写进《技术文档 1》）

① **分级（唯一四档，没有 R16）**：`files.rating` ∈ `all_ages | r15 | r18 | r18g`
（定义在 `app/config.py:RATING_LEVELS` / `RATING_TAG`）。

| rating | 库里 tag 的 name | 库里 tag 的 zh（= 磁盘首位标签） |
|---|---|---|
| all_ages | `全年龄` | 无（直接用 name） |
| r15 | `R15` | `R15（15禁）` |
| r18 | `R18` | `R18（18禁）` |
| r18g | `R18G` | `R18G（18禁猎奇）` |

⚠️ 容易踩：**磁盘文件名首位是中文显示名**（`[R15（15禁） 阿米娅 …]`），
**数据库里 tag 的 name 是 `R15`**。两端解析时必须走"分级别名表"归一（PC 端在 `app/library.py` 的
rating/alias 归一逻辑里，含 `15禁 / 18禁 / 健全 / G级` 这类历史脏词）。

② **中文显示名**：唯一实现是 `app/tag_i18n.py:label(name, zh_hint)`，优先级
`库里 tags.zh（含手填）` → `tag_zh_dict.json`（键为小写原 tag 名）→ 内置 WHOLE/TOKEN 拆词 → 英文原名；
脏数据（`/no_think` 之类）每层都会被 `is_usable_zh()` 挡掉。
两端要得到同样的中文名，平板必须存一份 `tags.zh`（在 `index.db` 里）+ 一份字典（随快照包或内置进 APK）。

③ 更正一条现有说法：**`app\tag_zh_corrections.json` 不是运行期词典**，它是词典生成流程的输入
（`tools/import_booru_zh.py` / `tools/apply_zh_corrections.py` 用），其内容已经合并进 `tag_zh_dict.json`
（实测 247 条修正键全部在词典里）。所以**平板上只需要 `tag_zh_dict.json` 一份**，不要为它再实现一套合并逻辑。

④ **分类（tag 类型）以数据库 `categories` 表为准**，不要硬编码 `config.TAG_CATEGORIES`：
当前库里已有 **18 个**分类（除 12 个内置的外，还有用户加的 `object / body_detail / text_ui / event / interaction / view / misc`），
界面分组、颜色、排序都要读库。

⑤ **标签层级**：父子从属存 `taxonomy_edges`（`parent_kind='tag' and child_kind='tag' and relation='sub_of'`，
父 = 更泛的标签，如 裙子 → 白裙子）；平行关联是 `relation='parallel'`（带 `weight`）。
规则：**检索父标签要带出所有子孙**（PC 端 `store.expand_tag_names()`）；**写盘/显示优先只留最具体的子标签**
（PC 端 `library.most_specific_tags()`，受设置 `tag_most_specific_on_disk` 控制）。

⑥ **磁盘命名规则**（`app/naming.py`，MIX 等文件管理器靠它识别）：
`<基础名> [tag1 tag2].jpg`；系列 `<系列名> [tag1 tag2]\001.jpg`；
标签块可多个、分隔符含空格 / 中英文逗号 / 分号 / 顿号；**标签内部不允许空格**（空格即分隔符）；
写入标签上限 `disk_max_tags=12`（系列文件夹 8 个），首位恒为分级。
回读用 `parse_tags_from_name()` / `split_name()`——平板若要做"文件名解析"，按这两条实现。

⑦ **系列**：`series.dir` 是相对 root 的目录名（**带标签，会随标签变化被改名**），
页码文件名是 `001.jpg` 这种；一页一文件，`files.series_id + page_no` 关联。
所以**平板上"这张图属于哪个系列"不能只靠文件名猜**，要读 `series` 表。

⑧ **标签状态**：`file_tags.status` ∈ `confirmed`（已生效）| `pending`（待审核）| 其他（拒绝类）。
平板默认只显示 `confirmed`；`manual`（人工确认过）和 `reviewed` 用于审核台视图。

### 2.6 传输方式（三选一，**未定**）

| 方案 | 平板拿到 | 优点 | 代价 |
|---|---|---|---|
| ① 离线快照包（拷到平板存储/SD） | index.db + thumbs（+可选原图） | 零常驻服务、断网可用、实现最简单 | 看新图要重新同步；原图占空间 |
| ② 局域网 HTTP（PC 端起小服务） | 索引 + 缩略图 + 原图按需拉 | 始终最新、可看大图、平板不占空间 | 要写并维护 PC 端服务 + 平板侧网络层 |
| ③ SMB 直读 `K:\` | 直接读原图，缩略图本地生成 | PC 端零改动 | Android SMB 客户端体验差、缩略图要本地生成（慢）、K: 要开共享 |

倾向：**v1 走 ①**（先把"能看、能搜"做出来，风险最低），② 作为 1.5 之后的增强；
③ 仅在用户已经在用 MIX 之类的 SMB 工具时才提。注意"看大图"必须有原图来源——320px 缩略图不够，
所以 ① 要么同步一份原图，要么配 ②/③ 按需取。

### 2.7 PC 端要新增的导出工具（接口草案）

```
python tools\export_snapshot.py --out "D:\_snapshot" [--edition auto|beta|stable] [--thumbs copy|skip] [--images all|none]
```

- 产出 `manifest.json` + `index.db`（按 2.3 裁剪，用 `VACUUM` 压一遍）+ `thumbs\`。
- 源库选择要用 `config.data_dir()` 的同一套规则（便携 `data/` 优先，否则 `%LOCALAPPDATA%\ImageTagStudio`），
  这样正式版/测试版都能导。
- **导出前必须复制一份库再裁剪**，绝不在用户的活库上动刀（沿用"改库前先备份 library.db"的约定）。

## 三、技术栈选型（建议 v0.1）

### 3.1 推荐方案：Kotlin + Jetpack Compose + Room + Coil

- Kotlin 2.x + **Jetpack Compose**（Material 3）：平板横竖屏自适应、大屏分栏、审核台并排布局都靠它最省事。
- **Room**：读 `index.db`（也可以直接 SQLite 只读打开，Room 的好处是查询类型安全 + Flow 自动刷新）。
- **Coil**：缩略图网格（几千张滚动要它来做内存/磁盘缓存）。
- **minSdk 26 / targetSdk 34 / compileSdk 34**：本机 SDK 就是 android-34（见 3.2）。
- 网络层（方案②时）用 OkHttp；本地读取走 SAF（用户选目录）或应用私有目录。

### 3.2 本机环境核实（2026-10-07 实测，不是"听说"）

| 项 | 现状 | 结论 |
|---|---|---|
| JDK | Oracle JDK 17.0.10（`java` 在 PATH） | 够用（AGP 8.x 要 17） |
| Android SDK | `C:\Android\Sdk`：`platforms\android-34`、`build-tools\34.0.0`、`cmdline-tools\latest` | 够用；`ANDROID_HOME` 未设，构建脚本里显式指路 |
| Gradle | `C:\Gradle\gradle-8.7`（未在 PATH，用绝对路径或 wrapper） | 与 AGP 8.5.2 匹配 |
| Gradle 缓存 | `%USERPROFILE%\.gradle\caches`：已有 AGP 8.5.2、Kotlin 插件、androidx.appcompat/recyclerview/constraintlayout/documentfile 等 | 传统 View 方案基本可离线构建 |
| Compose / Room / Coil | **缓存里没有** | 首次构建必须联网拉依赖 |
| 网络 | 代理 `http://127.0.0.1:29758` 在线，实测 `dl.google.com` maven 返回 200 | 拉依赖可行，Gradle 里要配代理 |
| 真机 | `adb devices` 当前为空（S24 Ultra 未连接） | 调试前要先连上/授权 |
| 参考项目 | `E:\文档\ChatGPT\死了么app 2`（Java + WebView 壳；`tools\build-apk.ps1` 甚至**不用 Gradle**，直接 `javac`+`d8`+`aapt2` 打包） | 说明本机有"零 Gradle"的兜底路子 |

### 3.3 备选与降级路径（如果 Compose 依赖拉不动/嫌重）

- **B. Kotlin/Java + 传统 View**（RecyclerView + ViewPager2 + ConstraintLayout）：缓存里已有大部分依赖，
  可离线构建，甚至能照 `死了么app 2` 的脚本不依赖 Gradle。代价：布局代码多、大屏适配要手写。
- **C. WebView 壳 + HTML/JS 前端**：复用现成构建脚本，UI 迭代最快，完全离线。代价：几千张图滚动与
  双指缩放/大图手势体验明显弱于原生，还要自己写缩略图缓存。

建议：**默认 A；只有首次构建被网络卡死时才降级 B**，C 不作为主线（图片浏览是核心体验）。

### 3.4 与手机端共用数据层（待与手机端会话对齐）

《技术文档 5》的建议是"一个 Android 工程、两套 UI 模块、共享数据层"。我们的意见一致，倾向：

```
:core-data      索引读取（Room/DAO）、tag 解析、分级归一、中文名映射、快照导入与校验、缩略图定位
:app-tablet     平板 UI（分栏、网格、审核台）
:app-phone      手机 UI（单列、检索优先）
```

理由：数据规则（尤其 2.5 那 8 条）一旦写成两份，必然漂移；词典 5.7 MB 也只该放一份。
`core-data` 里的规则要写成**可跑单元测试的纯函数**，用 PC 端导出的样例数据做对拍。

## 四、工程目录规划（草案）

建议**放在 PC 主程序仓库之外的同级目录**，避免污染 PC 端发布物（`tools/build_update.py` 只镜像
`app/ tools/ assets/ 说明书`，虽然塞进仓库也不会进更新包，但会进 git 历史与完整安装包）：

```
E:\文档\ChatGPT\图片标签分类_app\        ← Android 工程（新建 git 仓库）
  settings.gradle.kts                  include(":core-data", ":app-tablet", ":app-phone")
  gradle\libs.versions.toml            版本集中管理（AGP 8.5.2 / Kotlin 2.x / Compose BOM / Room / Coil）
  core-data\                           Kotlin 数据层（手机/平板共用，含单元测试）
    src\main\kotlin\...\model\         Rating / TagCategory / TagRelation / SnapshotManifest
    src\main\kotlin\...\db\            Room 实体与 DAO（对应 index.db）
    src\main\kotlin\...\rules\         TagName / RatingMap / ZhDict（对应 app/naming.py、tag_i18n.py）
    src\test\resources\snapshot_sample\ PC 端导出的小样例，用于对拍
  app-tablet\                          平板 UI
  app-phone\                           手机 UI（与手机端会话共享）
  tools\                               同步/安装脚本（adb push 快照包、调用 export_snapshot.py）
  docs\                                本端 README 与构建说明（技术文档仍以 PC 仓库 docs/ 为准）

PC 仓库（E:\文档\ChatGPT\图片标签分类）
  tools\export_snapshot.py             PC 端导出工具（2.7）
```

## 五、必须复用的既有规范（避免两端不一致）

- 分级只有四档：全年龄 / R15 / R18 / R18G（**没有 R16**）；别名归一表两端必须一致。
- tag 父子从属：检索父带子孙；显示/写盘优先只留最具体的子 tag。
- 中文显示：`tags.zh` → `tag_zh_dict.json` → 拆词 → 英文原名；运行期不需要 `tag_zh_corrections.json`。
- 分类：读库里的 `categories` 表，不硬编码。
- 系列：文件夹名带 tag、内部页码文件名；系列归属读 `series` 表，不靠文件名猜。
- 绝不改动用户原图；一切写操作先备份 `library.db`（平板端 v1 只读，写需求要走 PC 端约定）。

## 六、待确认项（冻结前必须定，逐条等结论）

1. **交换格式定稿**：是否采用"裁剪版 index.db + thumbs + manifest"（2.2 方案 A）？还是要 JSON 索引（方案 B）？
2. **传输方式**：离线快照包（①）/ 局域网 HTTP（②）/ SMB（③）？——直接决定 v1 的工作量和"能不能看大图"。
3. **数据源**：导出的源库用正式版（`%LOCALAPPDATA%\ImageTagStudio`，当前 21.9 MB、11144 文件行）
   还是测试版（`E:\...\data`）？图库目录是否确认只有 `K:\ImageTags`（来源 `K:\pictures`）？
4. **v1 是否只读**：要不要"随手打 tag 进待审核队列"（写路径）？如果要，写入走什么通道（导出待审文件 / HTTP 回传）。
5. **词典投放方式**：`tag_zh_dict.json`（5.7 MB）内置进 APK，还是随快照包更新？（随包更新更灵活，内置更省事）
6. **私有内容处理**：R18/R18G 是否要在平板上打码（对应 PC 端设置 `rating_blur`）、是否要单独加锁/隐藏。
7. **工程归属**：Android 工程放 `E:\文档\ChatGPT\图片标签分类_app\` 可以吗？手机端是否并入同一工程的两个 module
   （需与手机端会话对齐，见 3.4）。
8. **测试设备**：现在 `adb devices` 为空——平板型号是什么？S24 Ultra 是手机端的吧，平板端用自己的设备还是共用？
9. **范围与节奏**：1.5 是否包含平板端首个可用版本（只能看/搜），还是等 1.5 结束后单独立项？

## 七、变更记录

- 2026-10-07：流程变更（用户要求，已同步 `AGENTS.md` / 文档 1 / `开发计划.md`）——**只有用户本人明确说
  "推送 / 正式更新 / 发版"时才允许 push 与发布**；本端改完只做本地 commit 记录并汇报，不自动 push、
  不动 GitHub release、不主动同步 D 盘正式版副本。
- 2026-10-07：文档建立（尚未开工，等待 PC 端 1.5 的开发节奏）。
- 2026-10-07：写入《数据交换格式草案 v0.1》（快照包 = manifest + 裁剪 index.db + thumbs；含 8 条字段级约定）、
  《技术栈选型》（推荐 Kotlin + Compose + Room + Coil；核实 JDK17 / SDK34 / Gradle 8.7 / AGP 8.5.2 缓存 / 代理可达 dl.google.com）、
  《工程目录规划》（独立目录 + `core-data`/`app-tablet`/`app-phone` 三模块）。同时提出 9 项待确认项。
  未改任何代码；未改《技术文档 1》（跨端约定需 PC 端会话确认后再同步）。关键核实：
  缩略图键 = `md5(file_id|mtime|size)`（只有 DB 有 file_id，故 DB 快照优于 JSON）；
  `tag_zh_corrections.json` 不是运行期词典（已并入 `tag_zh_dict.json`，247/247 命中）→ 需更正文档 4/5 原表述；
  分类以库里 `categories` 表为准（现有 18 个，多于内置 12 个）。
