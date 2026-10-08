# DLC：AI 生图助手（comfyui_helper）待办与分工

> 来源：AI 生图会话 2026-10-08 回的清单（含工作量估算）；状态由 PC 端主程序会话维护。
> 宿主（可选安装机制）在 `app/dlc.py` + `app/ui/dlc_ui.py`，DLC 内容在 `dlc/comfyui_helper/`。

## 已完成（宿主侧）

- ✅ DLC 扫描 / 启用停用 / zip 安装 / 卸载进回收站 / 配置持久化（`settings.dlc_enabled`、`dlc_config`）
- ✅ 主窗口「更多 ▾ → 扩展包（DLC）…」管理窗口；**生图输出路径**就在里面配置（`output_dir`）
- ✅ `DlcHost` 给 DLC 的能力：`config/save_config`、`add_action`、`open_window`、
  `lexicon()`（共享词库）、`scan_into_library()`（生成结果入库）、`tag_files()`（自动打标）、`store/library/hub`
- ✅ DLC 当"包"加载（支持 `from .ui import …` 相对导入）
- ✅ 原生生图窗口骨架：模型/参数/输出路径/批量/结果/一键入库 + 词库按钮（中文→标签 / 词表校验 / 插入热词）

## 已完成（内容侧，AI 生图会话）

- ✅ `prompt_helper/`：中文 → danbooru 提示词（本机 Ollama 离线 + 23.3 万词表校验），附 HTTP 服务（8199）
- ✅ `workflows/anima_aesthetic.api.json`（UNETLoader + ModelSamplingAuraFlow + CLIPLoader）+ `workflows/presets.json`
- ✅ `models.json` 模型清单（SHA256；**模型不进包**，按需下载）
- ✅ `scripts/`：断点续传下载、批量出图、Anima 三件套
- ✅ `wordlists/`（gzip 8.07 MB）+ `swarmui_component/`（可选组件，默认不装）
- ✅ 中文映射已改为委托 `host.lexicon()`（`dictionary.py` 的 `attach_lexicon`）

## 待办（按建议顺序）

| # | 功能 | 说明 | 预估 | 状态 |
|---|---|---|---|---|
| 1 | 工作流下拉 | 把 `workflows/presets.json` 做成界面可选预设（现在只有模型类型切换 + 内置默认） | 2h | 待做 |
| 2 | 批量队列 | 队列化 + 可取消 + 断点续跑（`scripts/batch_gen.py` 已有跳过已出图的思路） | 3h | 待做 |
| 3 | 一键入库并自动打标 | 入库后接 `host.tag_files()` | 1h | 待做 |
| 4 | 按模型过滤词表 | NoobAI 14.2 万 / Illustrious 9.4 万 / Anima 10.8 万，加"当前模型认不认"的开关 | 2h | 待做 |
| 5 | 模型下载/校验入口 | 脚本齐全，缺界面入口与进度显示 | 2h | 待做 |
| 6 | 参考图 / 姿势控制 | IP-Adapter（xl + clip_h 本地已有）+ ControlNet++ SDXL union | 5h | 待做 |
| 7 | 局部重绘 / 换装 | inpaint 工作流（VAEEncodeForInpaint + denoise 0.5~0.65）+ 界面画遮罩（宿主配合） | 6h | 待做 |
| — | SwarmUI 组件 | 保持"可选组件、默认不装" | — | 维持 |

合计约 **20~25 小时**，可按批次给。

## 打包结论

- `wordlists/` **随 DLC 分发**：gzip 后 8.07 MB（原始 19.21 MB），整个 DLC 目录 8.23 MB，**不用分卷**。
- 安装器提供"可选安装 DLC"；更新包同步带 DLC 目录（见发布流程）。

## 词库共享（避免两套词表）

- **中文↔英文 / 分类 / 从属 / 热度**：一律走 `host.lexicon()`（`app/taglex.py`）。
- **"这个 tag 在目标模型里认不认"**：留在 DLC 的 `wordlists/`（模型相关知识）。
- 人工口头说法（danbooru 没覆盖的）：放共享别名表 `app/tag_zh_aliases.json`（两边共用，别再各写一份）。

### 2026-10-08 修的两个词库 bug（生图端报的）

1. `zh_to_tag("半身")` → `newhalf`：根因是 `tag_zh_dict.json` 里 **`newhalf` 被错译成"半身"**
   （`newhalf` 应为"人妖"）。已在 `tag_zh_corrections.json` 修正并应用（121 条），
   现在「半身」→ `upper_body`、「人妖」→ `newhalf`。
2. 长短语乱配（`"微笑看镜头"→newhalf`、`"半身"→asahina mirai`）：根因是 `zh_to_tag` 会退回
   带模糊/子串的解析。现在改成**只认精确匹配**，另加 `segment_zh()` 贪心切词：
   `"微笑看镜头"` → `smile, looking_at_viewer`；`prompt_fix("微笑看镜头，半身，冷色调")`
   → `smile, looking_at_viewer, upper_body, cool_colors`（0 未命中）。
3. 英文输入不再反查（`zh_to_tag("newhalf")` → `newhalf`，不会被别名劫持成别的标签）。
