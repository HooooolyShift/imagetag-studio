# 图片标签工坊 · 项目约定

## 断电纪律（**每天 23:00 断电**，2026-10-07 用户告知）

- **22:30 之后不要再开新功能**：那一刻起只做收尾——跑自检、把改动 commit 到本地、写清"做到哪、
  下一步是什么"，确保任何时刻断电都不会留下半成品或语法错误的文件。
- **长任务要分批**：模型下载、批量出图、打包、上传这类超过 ~20 分钟的任务，要么在 22:30 前能收尾，
  要么拆成可断点续做的小批（下载用 `-C -` 续传、出图按张分批、打包脚本可重跑）。
- 未提交的改动尽量别跨天留：宁可少做一点，也要让代码仓库处于"随时能跑"的状态。

## 发布流程（**只在用户明确要求时才推送/发布**）

> **2026-10-07 用户明确要求：之后只有他决定"正式更新"了，才允许推送与发布。**
> 也就是说：改完、验证完**不要自己 push、不要自己更 release**，把结果留在本地并汇报，
> 等用户说"推送 / 正式更新 / 发版"再执行下面的流程。日常提交（本地 commit）照常做，用来记录改动。
>
> **2026-10-08 用户决定：不再写更新公告、不再做宣传片**（宣传片线已归档，`docs/技术文档_2_宣传片制作.md` 停止更新）。
> 下面的发布流程里**已经没有公告/封面这两项**，别再按老流程去生成它们。

用户说了要正式更新时，按这个顺序做：

1. **先推送代码**：`git -c http.proxy=http://127.0.0.1:29758 push origin master`
   （代理固定用 `http://127.0.0.1:29758`；GitHub 凭据已在 Windows 凭据管理器里，
   API token 可用 `git credential fill` 取到，用户名 `HooooolyShift`。）
2. **重建两个包**：
   - 更新包：`python tools\build_update.py "K:\ImageTagStudio\更新包"` → 再压成 `K:\ImageTagStudio\更新包.7z`
   - 完整包：更新 `K:\ImageTagStudio\安装包\payload` 后重压 `K:\ImageTagStudio\安装包.7z`
   - 说明书有界面改动时重跑 `python tools\make_manual.py`
3. **GitHub Release**：在 `HooooolyShift/imagetag-studio` 建 release（tag 用 `v1.4` 这种格式），
   挂上这些资产：
   - `update_vX.Y.7z`（更新包，必挂）
   - `manual_vX.Y.pdf`（说明书）
   - **完整安装包超过 GitHub 单文件 2GB 上限，必须分卷**：
     用 `python tools\split_release.py "K:\ImageTagStudio\安装包" "<输出目录>"` 生成
     `payload.zip.001/.002/...` + `merge_and_install.bat`（由 `合并并安装.bat` 改名）+ `SHA256.txt`，全部挂到 release 上。
   - **不再挂公告/封面**（2026-10-08 起不写公告、不做宣传片）。
4. 发布完把 release 链接回给用户。

**注意**：`release` 上的资产别用旧包顶替——每次都要从当前 `K:\ImageTagStudio\安装包` 重新切分卷。
**但前提仍是用户已明确说"正式更新"**；没说就不推送、不动 release。

另外：D 盘正式版副本（`D:\图片标签分类`）也属于"正式发布"的一部分——
只有用户决定正式更新时，才执行 `git -C "D:\图片标签分类" pull --ff-only origin master` 把更新同步过去。

## 其它既有约定

- 交付前跑自检：`tools\uicheck.py`、`graphcheck.py`、`namingcheck.py`、
  `reviewtagcheck.py`、`reviewthumbcheck.py`、`tagfiltercheck.py`。
- 改动涉及磁盘文件的操作（移动/删除）一律走回收站或先备份，并且**绝不能动用户的原图**。
- 数据库改动前先备份 `%LOCALAPPDATA%\ImageTagStudio\library.db`。
