# 技术文档 5 ｜ 手机端 APP

> 负责人：手机端 APP 开发会话（新建，2026-10-07）
> **规矩**：每次做出决定（技术栈、目录、接口、数据库结构）或改动代码后，立刻写进本文件；
> 其他 4 份技术文档（PC 端 / AI 生图 / 宣传片 / 平板端）随时可读，**跨端共用的格式与约定以《技术文档 1》为准**。

## 一、目标与范围

手机竖屏优先的伴生端：随手浏览 / 检索 PC 端图库、查看某张图的 tag、按 tag 过滤，
后续可扩展"随手打 tag 进待审核队列"。与平板端共享数据层代码，只在布局与交互上分开。

**MVP（第一版）做**：单列 / 双列缩略图浏览、按 tag 检索（中文 / 英文 / 拼音）、分级过滤、
大图查看 + tag 列表、系列内翻页、分级安全设置。

**MVP 不做**：写回标签 / 改文件名 / 审核台 / 打标 / 人脸 / 重复图（这些留 PC 与平板端）。

## 二、与平板端的差异（约定）

- 平板：大屏分栏、批量操作、审核台体验优先。
- 手机：单列、滑动浏览、检索与查看优先；批量操作简化。
- **数据层（索引读取、tag 解析、分级、中文名映射）两端共用一套代码/规则**，避免出现第二套解析逻辑。

## 三、技术栈（2026-10-07 冻结草案）

| 项 | 取值 | 说明 |
|---|---|---|
| 语言 / UI | Kotlin + Jetpack Compose（Material 3） | 与《技术文档 4》一致，两端共享 Kotlin 数据层 |
| 构建 | Gradle 8.7 + AGP 8.5.2 + Kotlin 1.9.24（Version Catalog 统一管版本） | 与本机已有缓存对齐，少下载 |
| SDK | compileSdk 34 / targetSdk 34 / minSdk 26 | 本机只装了 `platforms/android-34` |
| Compose | Compose Compiler 1.5.14（配 Kotlin 1.9.24）+ Compose BOM 2024.06.00 | **需联网首次下载** |
| 图片加载 | Coil 2.6.0（Compose 版） | 若依赖下载受阻，回落 "BitmapFactory + LruCache" 自实现（零依赖） |
| 数据层 | **不引入 Room / KSP**：core 只放纯 Kotlin 模型 + `IndexReader` 接口 | 手机端是**只读索引**，Room 的迁移/事务能力用不上，却会多绑一层 KSP 版本 |
| 导航 | 单 Activity + Compose 状态导航（先不引 navigation-compose） | MVP 只有 4 个页面 |
| 真机 | S24 Ultra（adb 直连） | 当前 `adb devices` 为空，需要时插线即可 |

### 选型依据（本机实测，2026-10-07）

- Android SDK：`C:\Android\Sdk`（platform-tools / build-tools 34.0.0 / platforms android-34 / cmdline-tools，**无 Android Studio**）。
- JDK：`java 17.0.10`（Oracle）已在 PATH，带 `javac` / `keytool`，够 AGP 8.5 用。
- Gradle 依赖缓存：`C:\Users\27838\.gradle\caches` 已有 **AGP 8.5.2、Kotlin Gradle Plugin 1.9.24、Material 1.12.0、appcompat / recyclerview / viewpager2**；
  **但 `androidx.compose.*` 一个都没有**，Gradle 发行版本体也不在盘上（`.gradle/wrapper/dists` 为空）。
- 网络：`dl.google.com`、`repo1.maven.org`、`services.gradle.org` 实测可达（HTTP 200，Gradle 8.7 包 134 MB）；本机代理 `127.0.0.1:29758` 可用。
- 结论：走 Compose 需要一次性联网拉 **Gradle 8.7 发行版 + Compose 全家桶（粗估 300~400 MB）**，之后可离线增量构建。

### 被否 / 备选方案

1. **免 Gradle 手工链路**（复用 `E:\文档\ChatGPT\死了么app 2\tools\build-apk.ps1`：aapt2 → javac → d8 → zipalign → apksigner，零依赖秒级构建）。
   优点：不下载任何东西、已有成功先例、天然能处理中文路径（它会先把工程拷到 `%TEMP%` 的 ASCII 目录）。
   缺点：与 Compose 不兼容（Compose 编译器必须有 Gradle 插件），UI 只能写 Views / WebView，且与平板端的 Compose 约定分叉。
   **保留为"不允许联网下载"时的备选**，不作为首选。
2. **直接读 `library.db` 当唯一数据源**：省掉导出脚本，但库里有 `files.clip_vec` 等模型 BLOB（实测每张已打标图约 5 KB，
   1 万张即 ~50 MB）、`tag_probe` / `auto_json` 等手机完全用不到的数据，且手机侧任何误写会污染"模型学习数据"。
   → 降级为**调试用快速通道**，正式走导出包。

## 四、与 PC 端的数据交换（冻结草案）

与《技术文档 4》共用同一份格式（**不要在两端各定义一套**）。正式通道是 **PC 端导出的"手机包"**：

```
imagetag-mobile-<yyyymmdd>.zip
├─ manifest.json      # {format:"imagetag-mobile", format_version:1, generated_at, app_version,
│                      #  roots:[{id,label,path,is_library}], counts:{...},
│                      #  rating_levels:["all_ages","r15","r18","r18g"], blur_default:bool}
├─ tags.json          # [{id, name, zh, category, category_label, count, r18:0/1}]
├─ tag_tree.json      # {"child_map": {父标签: [子标签…]}}    ← most_specific 用
├─ zh_index.json      # {"中文名": "规范标签名"} = 内置词典 tag_zh_dict.json + corrections
│                      #  + tag_zh_fix_history.json + 库内手填 tags.zh（PC 端导出时合并好）
├─ pinyin_index.json  # 可选：拼音 → [规范标签名]（复用 app/pinyin_zh.json）
├─ files.jsonl        # 每行一张图：{id, root_id, rel, name, ext, w, h, rating,
│                      #  series_id, page_no, missing, tags:[规范名…], zh:[中文名…]}
├─ series.json        # [{id, dir, name, tags, page_count, first_file_id}]
└─ thumbs/<id%256>/<id>.jpg   # 320px JPEG（对齐 imaging.make_thumb 的尺寸档位）
```

要点：

- **文件名里的标签是中文（`disk_label`）**，包内同时给 `tags`（规范名）与 `zh`（中文），检索两种都能搜；
  中文名还原索引由 PC 端合并导出，**手机 / 平板不再各自解析 5.7 MB 的 `tag_zh_dict.json`**（两端一致，且省内存）。
- 分级是显式字段（`rating`），手机端**不必从文件名首位去猜**；四档且**没有 R16**。
- 缩略图沿用 PC 的 320 px 档；`files.rel` 保留相对路径，便于日后按 `rel` 映射到手机 / SD 卡上的原图。
- 导出脚本建议放 PC 端：`tools/export_mobile.py`（**属 PC 端会话的工作项，需转达排期**），
  生成后 zip 可直接用 adb push / 文件管理器拷进手机。

**原图访问分三档，MVP 只要求第 1 档**：

1. 纯离线包（只有缩略图）——MVP 默认，不依赖网络与 PC 常开。
2. SAF 授权 + `rel` 路径映射（用户把图库拷到手机 / SD 卡，按 `rel` 拼路径看原图）——M1 可选增强。
3. 局域网 HTTP（PC 端起小服务出原图）——《技术文档 4》也提过，需 PC 端排期，M2 再看。

## 五、与平板端共享的数据层（模块划分）

`core` 是唯一的数据层，规则与 PC 端 Python 实现**逐条对齐**，且必须两端一致：

| core 模块 | 职责 | 对齐 PC 端的 |
|---|---|---|
| `model/` | `Tag` / `ImageItem` / `Series` / `Rating`；`RatingLevels`（全年龄 / R15 / R18 / R18G，无 R16）与配色 | `config.RATING_*` |
| `parse/FileNameTags` | 解析文件名 / 文件夹名里的 `[tag tag]`，末尾可多块；非法字符替换与空格分隔规则 | `app/naming.py`（`split_name` / `parse_tags_from_name` / `sanitize_tag`） |
| `parse/ZhResolver` | 中文名 → 规范标签名（词典 + 修正 + 历史 + 库内手填，按"有图 > 下划线少 > 名字短"择优） | `app/tag_i18n.py`（`zh_index` / `_norm`） |
| `index/IndexReader` | **接口**：读 manifest / tags / files / series / zh_index | — |
| `index/JsonPackReader` | 读导出包的实现（MVP 默认） | — |
| `index/DbIndexReader` | 只读 `library.db`（框架自带 SQLite，无需 Room）——调试 / 平板端可选 | `app/store.py` 表结构 |
| `query/Search` | 中文 / 英文 / 拼音 / 分级 / 系列 过滤 | `app/library.py` 检索 |
| `query/MostSpecific` | 父子从属：有子标签就不显示父标签（带 visited 防环） | `library.most_specific_tags` |

**一致性铁律**：`core` 的任何改动，手机端与平板端两个会话都要各自在自己技术文档（5 / 4）记一条；
若与平板端实现冲突，**先对齐再改**；跨端格式最终以《技术文档 1》为准。

> ⚠️ **与《技术文档 4》的差异点（需平板端会话确认）**：文档 4 建议用 Room 读索引。
> 手机端的主张是 core 只暴露 `IndexReader` 接口，实现可替换：手机端用 JSON 包实现，平板端若坚持 Room，
> 就在 `app-tablet` 里写一个 Room 版 `IndexReader` 实现，**接口层保持一致即可**，不必强行统一 ORM。

## 六、工程目录规划

建议**新开一个仓库**，与 PC 端仓库分开（PC 仓库正在做 exe 打包与 release，别把 Android 工程塞进去）：

```
E:\文档\ChatGPT\图片标签工坊APP\      ← 新 Git 仓库（暂不接远端）
├─ settings.gradle.kts        [共享·冻结]  只有手机端会话改；平板端加模块要先说一声
├─ build.gradle.kts           [手机端]
├─ gradle.properties          [共享·冻结]  代理、JVM 内存、UTF-8
├─ gradle/libs.versions.toml  [共享·冻结]  版本号单一来源
├─ gradle/wrapper/            [共享·冻结]  Gradle 8.7
├─ core/src/main/kotlin/com/imagetag/core/   [共享]  见第五节模块表
├─ app-phone/                 [手机端]  竖屏单列 UI
├─ app-tablet/                [平板端会话]  分栏 / 审核 UI（手机端会话不碰）
├─ pack-sample/               [手机端]  小样本导出包（不含真实图），用于跑通与单测
├─ tools/                     [手机端]  build.cmd / push-sample.cmd / check-pack.py
└─ docs/                      [共享]  各自实现笔记；**结论仍回写 E:\…\图片标签分类\docs\技术文档 5**
```

- 构建产物目录用 `layout.buildDirectory = D:/ImageTagApp-build`（ASCII 路径）：
  这台机器上 `aapt2` / `javac` 这类原生工具在中文路径下出过打不开文件的问题，源码放哪都行，**构建目录务必 ASCII**。
- 版本号策略：`versionName` 跟 PC 端节奏（1.4 之后接 `1.5.0-mobile`），`versionCode` 自增。

## 七、里程碑

| 阶段 | 内容 | 产出 |
|---|---|---|
| M0 环境搭建 | Gradle 8.7 落地 + 空壳 Compose 工程跑通 + 装到 S24 Ultra | 能装能开的 debug APK |
| M1 数据层 | `core` 六个模块 + 单测（用 E 的 `测试图` 112 张生成小包验证解析） | core 通过单测 |
| M2 浏览检索 | 单列 / 双列缩略图、tag 检索、分级过滤 | 可用的浏览页 |
| M3 详情设置 | 大图页 + tag 列表、系列翻页、分级安全设置 | 功能齐 |
| M4 验收发布 | 真机验收 + 签名打包 | 可分发 APK |

## 八、风险与规避

| 风险 | 规避 |
|---|---|
| 中文 / 空格路径让原生构建工具报错 | 构建输出目录用 ASCII；必要时整个工程也放 ASCII 路径（先例：`死了么app 2` 会拷到 `%TEMP%` 再编译） |
| 首次要联网下载 Gradle + Compose（300~400 MB） | M0 一次性做完并缓存；不允许联网则改走免 Gradle 手工链路 + Views |
| Compose Compiler 与 Kotlin 版本强耦合 | 锁死 Kotlin 1.9.24 + Compose Compiler 1.5.14 + BOM 2024.06.00，写进版本目录不再乱升 |
| 手机端 / 平板端并发改 `core` 互相踩 | 按第六节的 `[共享·冻结]` 归属表改；core 改动必须两边都记变更记录 |
| 手机上只有缩略图，看不了原图 | MVP 先接受；原图按第四节的第 2、3 档逐步补 |
| 真机 S24 Ultra 与 PC 连接不稳 | 网盘 / 数据线拷包兜底，不把 adb 当唯一通路 |
| R18 / R18G 内容误显 | 默认模糊 + 设置项；沿用 PC 四档与配色，不自行新增档位 |

## 九、待确认项（等用户拍板）

1. **工程位置**：默认 `E:\文档\ChatGPT\图片标签工坊APP`（新 Git 仓库，独立于 PC 仓库）。要不要换到纯 ASCII 根目录（如 `D:\ImageTagApp`）？
2. **原图**：第一版接受"只能看缩略图"的离线包吗？还是要一上来就能看原图（得先定 SAF / SMB / 局域网 HTTP 走哪条）？
3. **联网**：允许 M0 一次性下载约 300~400 MB（Gradle 8.7 + Compose 依赖）吗？不允许就走免 Gradle 手工链路 + Views/WebView。
4. **Room**：手机端主张不引 Room / KSP，core 只留 `IndexReader` 接口；平板端是否同意（跨端一致性）？
5. **分级安全**：R18 / R18G 默认"模糊"还是"直接隐藏"？要不要加锁（PIN / 生物识别）？
6. **导出脚本**：`tools/export_mobile.py` 需 PC 端会话实现（手机 / 平板共用），能否转达排期？
7. **范围确认**：手机端第一批只做"浏览 + 检索"，写 tag / 审核留给平板端与 PC 端，是否同意？

## 十、变更记录

- 2026-10-07：文档建立（尚未开工）。
- 2026-10-07：完成技术准备并冻结草案。
  - 技术栈定型 Kotlin + Compose + Material 3 / Gradle 8.7 + AGP 8.5.2 + Kotlin 1.9.24 / compileSdk 34 · minSdk 26；
    **不引 Room / KSP**，数据层改为「纯 Kotlin 模型 + `IndexReader` 接口」。
    依据是本机实测：无 Android Studio、SDK 只有 android-34、AGP 8.5.2 与 Kotlin 1.9.24 已在 Gradle 缓存、
    Compose 与 Gradle 发行版本体需联网、`dl.google.com` / `services.gradle.org` 与代理 `127.0.0.1:29758` 均可达。
  - 交换格式冻结为 PC 端导出的 `imagetag-mobile-*.zip`（manifest / tags / tag_tree / zh_index / pinyin_index /
    files.jsonl / series / thumbs），原图访问分三档，MVP 只做离线缩略图。
  - 划定 `core` 六个模块，并写明与 PC 端 Python 实现（`naming.py` / `tag_i18n.py` / `most_specific_tags` / `RATING_*`）的逐项对齐关系。
  - 确立工程目录与文件所有权表；构建产物目录固定 ASCII（`D:\ImageTagApp-build`）。
  - 列出 7 项待用户确认（见第九节），**尚未动工写代码**。
