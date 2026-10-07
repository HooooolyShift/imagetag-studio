"""各类对话框：标签管理、人物、系列、设置、预览（含手动框选标注）、模型下载。"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QButtonGroup, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QRadioButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from .. import categories, models as model_lib
from ..config import CATEGORY_ORDER, SOURCE_LABELS, TAG_CATEGORIES
from ..engines.wd14 import guess_category
from ..workers import Task
from .common import label, load_pixmap, ok_cancel


class CategoryNewDialog(QDialog):
    """就地新建标签类型：名字 + CLIP 提示词模板。"""

    def __init__(self, store, parent=None, name: str = ""):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("新建标签类型")
        self.resize(520, 340)
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("例如：道具 / 兽人 / 场景道具（中文英文都行）")
        form.addRow("类型名称", self.name)
        self.tmpl = QPlainTextEdit()
        self.tmpl.setPlainText("a {} object\n{}\nholding {}")
        form.addRow("CLIP 提示词模板（每行一条）", self.tmpl)
        tip = QLabel("用 <code>{}</code> 代表标签名。模板只影响「CLIP 零样本识别」，"
                     "不影响你已经打好的标签；留空则用通用模板。")
        tip.setWordWrap(True)
        form.addRow("", tip)
        ok_cancel(self, form)

    def values(self) -> tuple[str, list[str]]:
        return (self.name.text().strip(),
                [t.strip() for t in self.tmpl.toPlainText().splitlines() if t.strip()])


class CategoryCombo(QComboBox):
    """标签类型选择框：可编辑，列出库里所有类型，也能就地新建。

    - 直接选已有类型；
    - 输入一个不存在的名字 → 弹「新建类型」小窗（可配提示词模板）；
    - 或点列表里的「＋ 新建类型…」。
    - include_all=True 时最前面多一项「全部（不限分类）」，用来让标签列表/联想显示全量；
      这时它本身的取值由 all_means 决定（默认归到「其它」）。
    取用请调用 ensure_current()，它会保证返回的类型在库里真实存在。
    """

    NEW_SENTINEL = "__new_category__"
    ALL_TEXT = "全部（不限分类）"

    def __init__(self, store=None, current: str | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.include_all = False       # 是否在最前面放「全部（不限分类）」项
        self.all_means: str | None = None   # 选中「全部」时 ensure_current() 返回的类型
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.lineEdit().setPlaceholderText("选一个类型，或输入新类型名")
        self.reload(select=current)
        self.activated.connect(self._on_activated)

    def reload(self, select: str | None = None) -> None:
        cur = self.currentData() if self.count() else None
        if select is not None:
            target = select
        elif cur is not None:
            target = cur            # 可能是 ""（「全部」项），下面会把它重新插回列表头部
        else:
            target = "other"
        self.blockSignals(True)
        self.clear()
        if self.store is not None:
            for c in categories.ordered(self.store):
                self.addItem(f"{c['label']} ({c['key']})" + (f"  〔{c['count']}〕" if c["count"] else ""),
                             c["key"])
        else:
            for key in CATEGORY_ORDER:
                self.addItem(f"{TAG_CATEGORIES[key]} ({key})", key)
        self.addItem("＋ 新建类型…", self.NEW_SENTINEL)
        if self.include_all:
            self.insertItem(0, self.ALL_TEXT, "")
        self.blockSignals(False)
        idx = self.findData(target)
        self.setCurrentIndex(idx if idx >= 0 else 0)

    def _on_activated(self, index: int) -> None:
        if self.itemData(index) == self.NEW_SENTINEL:
            self.ensure_current()

    def _ask_new(self, name: str) -> tuple[str, list[str]] | None:
        dlg = CategoryNewDialog(self.store, self, name)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.values()

    def ensure_current(self, ask=None) -> str:
        """返回当前类型 key；若是新名字则先建出来（ask 可注入用于测试）。"""
        if self.store is None:
            return self.currentData() or "other"
        data = self.currentData()
        if data == "":            # 选中了「全部（不限分类）」→ 不靠它决定类型
            return self.all_means or "other"
        text = self.currentText().strip()
        if data and data != self.NEW_SENTINEL:
            # 文本被改过就按新名字处理，否则用选中的 key
            label = categories.label_of(self.store, data)
            if text.startswith(label) or not text:
                return data
        name = text or ""
        if not name:
            return "other"
        exist = {c["label"]: c["key"] for c in categories.ordered(self.store)}
        if name in exist:
            self.reload(select=exist[name])
            return exist[name]
        ask = ask or self._ask_new
        res = ask(name)
        if not res:
            self.reload(select="other")
            return "other"
        new_label, templates = res
        key = categories.add(self.store, new_label or name, templates or None)
        self.reload(select=key)
        return key


def category_combo(store=None, current: str | None = None, include_all: bool = False,
                   all_means: str | None = None):
    """类型选择框。

    include_all=True 时最前面多一项「全部（不限分类）」（值为空字符串），
    用于让旁边的标签列表/联想显示全量；选它时 ensure_current() 返回 all_means（默认「其它」）。
    """
    cb = CategoryCombo(store, current)
    cb.include_all = bool(include_all)
    cb.all_means = all_means
    if include_all:
        cb.reload(select=current if current is not None else "")
        cb.setToolTip("选一个分类 → 标签框只列该分类下的标签；选「全部（不限分类）」→ 标签框列全部标签")
    return cb


class CategoryManagerDialog(QDialog):
    """类型管理：新增/改名/删除类型，并给每个类型自定义 CLIP 提示词模板。"""

    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("标签类型管理（类型决定 CLIP 提示词模板）")
        self.resize(900, 620)
        v = QVBoxLayout(self)
        tip = QLabel("模板里用 <b>{}</b> 代表标签名，可以写多条（用 <b>|</b> 分隔），会一起平均。\n"
                     "例：服装类 → <code>wearing {} | {} clothing</code>；道具类 → <code>a {} object | {} | holding {}</code>\n"
                     "删掉某个类型时，它下面的标签会自动转到「其它」，标签本身不会丢。")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["类型名称", "key", "标签数", "CLIP 提示词模板（用 | 分隔）"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 60)
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot in (("新增类型", self.add_type), ("删除选中类型", self.del_type),
                           ("上移", lambda: self.move(-1)), ("下移", lambda: self.move(1)),
                           ("保存修改", self.save_all)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        v.addLayout(row)
        self.reload()

    def reload(self) -> None:
        self._loading = True
        items = categories.ordered(self.store)
        self.table.setRowCount(len(items))
        for i, c in enumerate(items):
            it = QTableWidgetItem(c["label"])
            it.setData(Qt.UserRole, c["key"])
            self.table.setItem(i, 0, it)
            k = QTableWidgetItem(c["key"])
            k.setFlags(k.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 1, k)
            n = QTableWidgetItem(str(c["count"]))
            n.setFlags(n.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 2, n)
            self.table.setItem(i, 3, QTableWidgetItem(" | ".join(c["templates"])))
        self._loading = False

    def add_type(self) -> None:
        name, ok = QInputDialog.getText(self, "新增类型", "类型名称（中文或英文都行）：")
        if not ok or not name.strip():
            return
        key = categories.add(self.store, name.strip())
        self.reload()
        self.changed.emit()
        QMessageBox.information(self, "已新增", f"类型「{name.strip()}」已创建（key={key}）。\n"
                                              "可以给它改提示词模板，然后在审核/标签编辑里选用。")

    def del_type(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        key = self.table.item(row, 1).text()
        label = self.table.item(row, 0).text()
        n = int(self.table.item(row, 2).text() or 0)
        if QMessageBox.question(self, "删除类型",
                                f"删除类型「{label}」？它下面的 {n} 个标签会转到「其它」。") != QMessageBox.Yes:
            return
        categories.remove(self.store, key)
        self.reload()
        self.changed.emit()

    def move(self, delta: int) -> None:
        row = self.table.currentRow()
        items = categories.ordered(self.store)
        if row < 0 or not items:
            return
        new_row = max(0, min(len(items) - 1, row + delta))
        if new_row == row:
            return
        order = [c["key"] for c in items]
        order.insert(new_row, order.pop(row))
        for i, k in enumerate(order):
            self.store.update_category(k, sort=i * 10)
        self.reload()
        self.table.setCurrentCell(new_row, 0)
        self.changed.emit()

    def save_all(self) -> None:
        for i in range(self.table.rowCount()):
            key = self.table.item(i, 1).text()
            label = self.table.item(i, 0).text().strip()
            raw = self.table.item(i, 3).text()
            templates = [t.strip() for t in raw.split("|") if t.strip()]
            self.store.update_category(key, label=label or key, templates=templates or ["{}"])
        self.reload()
        self.changed.emit()
        QMessageBox.information(self, "已保存", "类型名称与提示词模板已保存（新打的标签立刻按新模板识别）。")


# --------------------------------------------------------------------------- 标签
class TagEditDialog(QDialog):
    """新建/编辑单个标签：名称 + 类型 + CLIP 提示词。"""

    def __init__(self, parent=None, name: str = "", category: str = "other", prompt: str = "",
                 auto: bool = True, title: str = "新建标签", requires: str = "", store=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.name_edit = QLineEdit(name)
        form.addRow("标签名", self.name_edit)
        default_cat = category or "other"
        if store is not None:
            from .common import attach_tag_completer, bind_category_filter
            attach_tag_completer(self.name_edit, store)
        # 「全部」= 标签名联想不限分类；选具体类型则只联想该类型下的标签。
        # 选「全部」时保存的类型仍是当前类型（不因为筛选而把标签挪到「其它」）。
        self.cat = category_combo(store, "" if default_cat in ("", "other") else default_cat,
                                  include_all=True, all_means=default_cat)
        form.addRow("标签类型", self.cat)
        if store is not None:
            bind_category_filter(self.cat, self.name_edit, store)
        self.prompt_edit = QLineEdit(prompt)
        self.prompt_edit.setPlaceholderText("可留空；也可填英文提示词或用逗号写整句，例如 hatsune_miku")
        form.addRow("识别提示词", self.prompt_edit)
        self.requires_edit = QLineEdit(requires)
        self.requires_edit.setPlaceholderText("前置条件标签，空格分隔，例如：人物 上半身（不满足就不打这个标签）")
        form.addRow("前置条件", self.requires_edit)
        self.auto_cb = QCheckBox("参与自动识别（CLIP 零样本打标）")
        self.auto_cb.setChecked(auto)
        form.addRow("", self.auto_cb)
        tip = QLabel("提示：填 <b>hatsune_miku</b> 这类 WD14 英文标签名，可以把 WD14 的结果直接映射到这个中文标签上。")
        tip.setWordWrap(True)
        form.addRow("", tip)
        ok_cancel(self, form)
        self.name_edit.textChanged.connect(self._guess)

    def _guess(self, text: str) -> None:
        if (self.cat.currentData() or "other") == "other" and not self.prompt_edit.text():
            cat = guess_category(text.strip().replace(" ", "_"), 0)
            idx = self.cat.findData(cat)
            if cat != "other" and idx >= 0:
                self.cat.setCurrentIndex(idx)

    def values(self) -> dict:
        return {"name": self.name_edit.text().strip(), "category": self.cat.ensure_current(self),
                "prompt": self.prompt_edit.text().strip(), "auto": 1 if self.auto_cb.isChecked() else 0,
                "requires": self.requires_edit.text().strip()}


class TagManagerDialog(QDialog):
    """标签总表：增删改、合并、设类型/提示词，改完可让 CLIP 重新打分。"""

    rescoreRequested = Signal()
    tagCreated = Signal(str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("标签管理")
        self.resize(940, 620)
        self._loading = False
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标签…")
        self.search.textChanged.connect(self.reload)
        top.addWidget(self.search, 1)
        self.cb_group = QCheckBox("按分类折叠显示")
        self.cb_group.setToolTip("按图谱里的分类分组，点分组标题即可折叠/展开该分类下的标签")
        self.cb_group.stateChanged.connect(self.reload)
        top.addWidget(self.cb_group)
        for text, slot in (("新增标签", self.add_tag), ("删除选中", self.delete_selected),
                           ("合并到上一个", self.merge_selected), ("编辑选中", self.edit_selected),
                           ("类型管理…", self.manage_categories)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        v.addLayout(top)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["标签", "中文名（备注）", "类型", "所属作品", "图片数",
                                              "自动识别", "CLIP 提示词", "前置条件"])
        self.table.setColumnCount(8)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 140)
        self.table.setColumnWidth(4, 55)
        self.table.setColumnWidth(5, 65)
        self.table.setColumnWidth(6, 280)
        self.table.setColumnWidth(7, 130)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellClicked.connect(self._on_group_click)
        self.table.setSortingEnabled(False)
        self._limit_note = QLabel("")
        self._limit_note.setStyleSheet("color:#ffcc66;")
        v.addWidget(self._limit_note)
        v.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.info = QLabel("")
        bottom.addWidget(self.info, 1)
        b_rescore = QPushButton("用 CLIP 对新标签重新打分（秒级）")
        b_rescore.clicked.connect(self.rescoreRequested.emit)
        bottom.addWidget(b_rescore)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        bottom.addWidget(b_close)
        v.addLayout(bottom)
        self.reload()

    def reload(self) -> None:
        """只渲染前 N 行（其余靠搜索框定位）。

        以前每行都建一个下拉框控件，标签上千时创建上万个 QWidget → 打开就卡住。
        """
        LIMIT = 400
        self._loading = True
        all_rows = self.store.search_tags(self.search.text().strip())      # 中文（含词典译名）也能搜
        rows = all_rows[:LIMIT]
        self._limit_note.setText(
            (f"标签共 {len(all_rows)} 个，为流畅只列出前 {LIMIT} 个 —— 用上方搜索框找具体标签"
             if len(all_rows) > LIMIT else ""))
        # 多分类：一个标签可能连到多个分类节点（图谱允许连多条线），这里全列出来
        multi = self.store.tag_name_groups()

        def cat_text(t) -> str:
            names = multi.get(t["name"])
            if names:
                return " / ".join(names[:3])
            from .. import categories as _c
            return _c.label_of(self.store, t["category"])

        grouped = self.cb_group.isChecked()
        # 搜索时把"平行关联"的低关联那一个也带出来（先显示命中的，再跟一条带标记的）
        q = self.search.text().strip()
        if q:
            have = {int(t["id"]) for t in rows}
            extra_rows: list = []
            for t in rows:
                for p in self.store.tag_parallels(int(t["id"])):
                    pid = int(p["id"])
                    if pid in have:
                        continue
                    row_p = self.store.one("SELECT * FROM tags WHERE id=?", (pid,))
                    if row_p is not None:
                        have.add(pid)
                        extra_rows.append((t["name"], row_p))
            if extra_rows:
                merged = list(rows)
                for after_name, row_p in extra_rows:
                    idx = next((k for k, t in enumerate(merged) if t["name"] == after_name), len(merged) - 1)
                    merged.insert(idx + 1, row_p)
                rows = merged
                # 下面的排版是从 all_rows 重新生成的，所以这里必须一起补上，
                # 否则"平行关联的另一个"会被丢掉（搜索结果里看不到低关联那个）
                all_rows = list(all_rows) + [row_p for _n, row_p in extra_rows]
        # 所属作品：tag→tag 从属里的"作品"父级。
        # 作品和人物现在合成一个分类了，所以靠名字判定：父标签名出现在子标签的「_(名字)」后缀里
        # （amiya_(arknights) → 作品就是 arknights），比"有没有多个子项"更准，不会把服装父类误认成作品。
        parent_map = self.store.tag_parent_map()
        works_of = {}
        for child, parents in parent_map.items():
            hit = [p for p in parents
                   if f"_({p})" in str(child) or f"（{p}）" in str(child)]
            if hit:
                works_of[child] = hit
        works_of = {k: v for k, v in works_of.items() if v}
        # 作品的"特殊显示"：作品行做小标题，它名下的人物缩进排在下面。
        # 注意要在 **截断到 400 行之前** 排好，否则作品/人物会被切掉
        #   arknights → 它的人物 → 下一部作品 → … → 其余标签
        work_children: dict[str, list] = {}
        for t in all_rows:
            w = works_of.get(t["name"])
            if w:
                work_children.setdefault(w[0], []).append(t)
        all_names = {t["name"] for t in all_rows}
        work_heads = [w for w in work_children if w in all_names]
        display: list[tuple] = []
        if grouped:
            for t in sorted(all_rows, key=lambda t: (cat_text(t), t["name"])):
                display.append(("tag", t, 0, cat_text(t)))
        else:
            shown_ids: set[int] = set()
            for w in work_heads:
                display.append(("work", w, 0, None))
                for t in work_children[w]:
                    if int(t["id"]) in shown_ids:
                        continue
                    shown_ids.add(int(t["id"]))
                    display.append(("tag", t, 1, w))
            for t in all_rows:
                # 只跳过"已经作为作品子项渲染过"的；作品本身不在结果里时，它的人物照样要显示
                if int(t["id"]) in shown_ids or t["name"] in work_heads:
                    continue
                display.append(("tag", t, 0, None))
        total_display = len(display)
        display = display[:LIMIT]
        self.table.setRowCount(len(display))
        i = 0
        last_cat = None
        self._group_rows: dict[str, list[int]] = {}

        def put_work_head(row: int, work: str) -> int:
            """作品行：粗体小标题（点它可折叠下面的人物）。"""
            head_txt = f"▼ {work}　·　{len(work_children.get(work, []))} 个人物（点这行折叠/展开）"
            head = QTableWidgetItem(head_txt)
            head.setData(Qt.UserRole, ("work", work))
            head.setBackground(QColor("#2f3a52"))
            f = head.font()
            f.setBold(True)
            head.setFont(f)
            for c in range(self.table.columnCount()):
                self.table.setItem(row, c, head if c == 0 else QTableWidgetItem(""))
            self.table.setSpan(row, 0, 1, self.table.columnCount())
            self._group_rows.setdefault("work:" + work, [])
            return row + 1

        for kind, payload, indent, group_key in display:
            if kind == "work":
                i = put_work_head(i, payload)
                continue
            t = payload
            cat = cat_text(t)
            if grouped and cat != last_cat:
                last_cat = cat
                head = QTableWidgetItem(f"▼ {cat}（点这行折叠/展开）")
                head.setData(Qt.UserRole, ("group", cat))
                head.setBackground(QColor("#2b3d52"))
                for c in range(self.table.columnCount()):
                    self.table.setItem(i, c, head if c == 0 else QTableWidgetItem(""))
                self.table.setSpan(i, 0, 1, self.table.columnCount())
                self._group_rows.setdefault(cat, [])
                i += 1
            i = self.put_tag_row(i, t, cat, works_of.get(t["name"], []), indent=indent,
                                 group_key=(cat if grouped else ("work:" + group_key if group_key else None)))
        self._loading = False
        self.info.setText(f"共 {len(all_rows)} 个标签"
                          + ("（按分类分组，可折叠）" if grouped
                             else f"（按作品分组：{len(work_heads)} 部作品）")
                          )

    def _on_group_click(self, row: int, _col: int) -> None:
        """点分组标题行 → 折叠/展开该分类下的标签。"""
        item = self.table.item(row, 0)
        data = item.data(Qt.UserRole) if item else None
        if not isinstance(data, tuple) or data[0] != "group":
            return
        cat = data[1]
        shown = self._group_rows.get(cat, [])
        hide = any(not self.table.isRowHidden(r) for r in shown)
        for r in shown:
            self.table.setRowHidden(r, hide)
        if cat.startswith("work:"):
            item.setText(("▶ " if hide else "▼ ") +
                         item.text().split("（点这行折叠/展开）")[0].split("▼ ")[-1].split("▶ ")[-1]
                         + "（点这行折叠/展开）")
        else:
            item.setText(("▶ " if hide else "▼ ") + f"{cat}（点这行折叠/展开）")

    def put_tag_row(self, row: int, t, cat: str, works: list[str], indent: int = 0,
                    group_key: str | None = None) -> int:
        """渲染一行标签（indent=1 表示它是某个作品下的人物，向右缩进）。"""
        from .. import tag_i18n
        it_name = QTableWidgetItem(("　　└ " if indent else "") + tag_i18n.display(t["name"], t["zh"] or ""))
        it_name.setToolTip(f"{t['name']}（双击行或用「编辑选中」改类型/提示词）")
        it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
        it_name.setData(Qt.UserRole, int(t["id"]))
        self.table.setItem(row, 0, it_name)
        it_zh = QTableWidgetItem(t["zh"] or "")
        it_zh.setToolTip("这一列就是写进文件名的中文名（留空则用内置词典的翻译）；改完点「写回文件名」生效")
        it_zh.setData(Qt.UserRole, int(t["id"]))
        self.table.setItem(row, 1, it_zh)
        cat_item = QTableWidgetItem(cat)
        cat_item.setData(Qt.UserRole, t["category"])
        cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 2, cat_item)
        it_work = QTableWidgetItem(" / ".join(works))
        it_work.setToolTip("这个标签挂在哪个作品下（图谱里是 作品 → 人物 的从属连线）")
        it_work.setFlags(it_work.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 3, it_work)
        it_cnt = QTableWidgetItem(str(t["count"]))
        it_cnt.setFlags(it_cnt.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 4, it_cnt)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        chk.setCheckState(Qt.Checked if t["auto"] else Qt.Unchecked)
        self.table.setItem(row, 5, chk)
        self.table.setItem(row, 6, QTableWidgetItem(t["prompt"] or ""))
        self.table.setItem(row, 7, QTableWidgetItem(t["requires"] or ""))
        if group_key:
            self._group_rows.setdefault(group_key, []).append(row)
        return row + 1

    def _row_tag_id(self, row: int) -> int | None:
        item = self.table.item(row, 0)
        return int(item.data(Qt.UserRole)) if item else None

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        tid = self._row_tag_id(item.row())
        if not tid:
            return
        if item.column() == 5:
            self._set(tid, auto=1 if item.checkState() == Qt.Checked else 0)
        elif item.column() == 1:                     # 中文名：改完顺手刷新第一列的显示
            self._set(tid, zh=item.text().strip())
            from .. import tag_i18n
            row = self.store.one("SELECT name FROM tags WHERE id=?", (tid,))
            if row:
                self._loading = True
                self.table.item(item.row(), 0).setText(
                    tag_i18n.display(str(row["name"]), item.text().strip()))
                self._loading = False
        elif item.column() in (6, 7):
            field = {6: "prompt", 7: "requires"}[item.column()]
            self._set(tid, **{field: item.text()})

    def _set(self, tag_id: int, **kw) -> None:
        self.store.update_tag(tag_id, **kw)

    def add_tag(self) -> None:
        dlg = TagEditDialog(self, store=self.store)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            if not v["name"]:
                return
            self.store.save_tag(v["name"], v["category"], v["prompt"], v["auto"], v.get("requires", ""))
            self.tagCreated.emit(v["name"])
            self.reload()

    def edit_selected(self) -> None:
        row = self.table.currentRow()
        tid = self._row_tag_id(row) if row >= 0 else None
        if not tid:
            return
        row_data = self.store.one("SELECT * FROM tags WHERE id=?", (tid,))
        dlg = TagEditDialog(self, row_data["name"], row_data["category"], row_data["prompt"] or "",
                            bool(row_data["auto"]), "编辑标签", row_data["requires"] or "",
                            store=self.store)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            if v["name"] and v["name"] != row_data["name"]:
                self.store.rename_tag(tid, v["name"])
            self.store.update_tag(tid, category=v["category"], prompt=v["prompt"], auto=v["auto"],
                                  requires=v["requires"])
            self.reload()

    def delete_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        ids = [self._row_tag_id(r) for r in rows if self._row_tag_id(r)]
        if not ids:
            return
        if QMessageBox.question(self, "删除标签", f"确认删除 {len(ids)} 个标签？（图片本身不受影响）") != QMessageBox.Yes:
            return
        for tid in ids:
            self.store.delete_tag(tid)
        self.reload()

    def merge_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if len(rows) < 2:
            QMessageBox.information(self, "合并", "请选中 2 行以上，第一个作为保留的标签。")
            return
        ids = [self._row_tag_id(r) for r in rows]
        keep = ids[0]
        for tid in ids[1:]:
            if tid and keep:
                self.store.merge_tags(tid, keep)
        self.reload()

    def manage_categories(self) -> None:
        dlg = CategoryManagerDialog(self.store, self)
        dlg.changed.connect(self.reload)
        dlg.exec()
        self.reload()


# --------------------------------------------------------------------------- 人物
class PersonDialog(QDialog):
    """真人照片：人脸聚类 → 命名 → 自动给照片打上人物标签。"""

    changed = Signal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.store = library.store
        self.setWindowTitle("人物管理（真人）")
        self.resize(1000, 660)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.status = QLabel("")
        top.addWidget(self.status, 1)
        top.addWidget(QLabel("聚类阈值(小=更严格)"))
        self.eps = QDoubleSpinBox()
        self.eps.setRange(0.2, 0.9)
        self.eps.setSingleStep(0.05)
        self.eps.setDecimals(2)
        self.eps.setValue(library.settings.face_cluster_eps)
        top.addWidget(self.eps)
        self.b_cluster = QPushButton("重新聚类")
        self.b_cluster.clicked.connect(self.do_cluster)
        top.addWidget(self.b_cluster)
        v.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.people = QListWidget()
        self.people.currentItemChanged.connect(lambda *_: self.load_faces())
        lv.addWidget(self.people, 1)
        btns = QHBoxLayout()
        b_rename = QPushButton("命名…")
        b_rename.clicked.connect(self.rename_person)
        b_merge = QPushButton("合并到上一个")
        b_merge.clicked.connect(self.merge_person)
        b_del = QPushButton("删除")
        b_del.clicked.connect(self.delete_person)
        for b in (b_rename, b_merge, b_del):
            btns.addWidget(b)
        lv.addLayout(btns)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.face_list = QListWidget()
        self.face_list.setViewMode(QListWidget.IconMode)
        self.face_list.setIconSize(QSize(112, 112))
        self.face_list.setGridSize(QSize(124, 146))
        self.face_list.setResizeMode(QListWidget.Adjust)
        self.face_list.itemDoubleClicked.connect(self.open_face_source)
        rv.addWidget(self.face_list, 1)
        rv.addWidget(label("双击人脸可打开所在图片", "#8f96a3"))
        split.addWidget(right)
        split.setSizes([360, 640])
        v.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.counts = QLabel("")
        bottom.addWidget(self.counts, 1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        bottom.addWidget(b_close)
        v.addLayout(bottom)
        self.reload()

    # ---- 数据 ----
    def reload(self) -> None:
        self.people.clear()
        rows = self.store.persons()
        named = sum(1 for r in rows if r["name"])
        self.counts.setText(f"人脸 {self.store.stats()['faces']} 个 / 人物簇 {len(rows)} 个（已命名 {named}）")
        for r in rows:
            name = r["name"] or "(未命名)"
            it = QListWidgetItem(f"{name}   [{r['n']}张]")
            it.setData(Qt.UserRole, int(r["id"]))
            if not r["name"]:
                it.setForeground(QColor("#9aa0ac"))
            self.people.addItem(it)
        self.load_faces()

    def current_person(self) -> int | None:
        it = self.people.currentItem()
        return int(it.data(Qt.UserRole)) if it else None

    def load_faces(self) -> None:
        self.face_list.clear()
        pid = self.current_person()
        if pid is None:
            return
        rows = self.store.query("SELECT * FROM faces WHERE person_id=? LIMIT 200", (pid,))
        for r in rows:
            pm = self._face_pixmap(int(r["file_id"]), json.loads(r["bbox"] or "[]"))
            if pm is None:
                continue
            it = QListWidgetItem()
            from PySide6.QtGui import QIcon
            it.setIcon(QIcon(pm))
            it.setData(Qt.UserRole, int(r["file_id"]))
            self.face_list.addItem(it)

    def _face_pixmap(self, file_id: int, bbox) -> QPixmap | None:
        row = self.store.one("SELECT path FROM files WHERE id=?", (file_id,))
        if not row:
            return None
        img = load_pixmap(row["path"]).toImage()
        if img.isNull():
            return None
        w, h = img.width(), img.height()
        if len(bbox) >= 4:
            x1, y1, x2, y2 = bbox[:4]
            pad_w = (x2 - x1) * 0.25
            pad_h = (y2 - y1) * 0.25
            rect = QRect(int(max(0, x1 - pad_w)), int(max(0, y1 - pad_h)),
                         int((x2 - x1) + 2 * pad_w), int((y2 - y1) + 2 * pad_h))
        else:
            rect = QRect(0, 0, w, h)
        rect = rect.intersected(QRect(0, 0, w, h))
        if rect.isEmpty():
            return None
        return QPixmap.fromImage(img.copy(rect)).scaled(112, 112, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def open_face_source(self) -> None:
        it = self.face_list.currentItem()
        if not it:
            return
        fid = int(it.data(Qt.UserRole))
        row = self.store.one("SELECT path FROM files WHERE id=?", (fid,))
        if row:
            PreviewDialog(self.store, row["path"], self).exec()

    # ---- 操作 ----
    def do_cluster(self) -> None:
        self.library.settings.face_cluster_eps = float(self.eps.value())
        self.b_cluster.setEnabled(False)

        def job(progress, cancel, item):
            progress("人脸聚类…", -1.0)
            return self.library.cluster_faces(float(self.eps.value()), progress)

        t = Task(job, self, "人脸聚类")
        t.done.connect(lambda r: (self.status.setText(f"完成：{r}"), self.b_cluster.setEnabled(True), self.reload()))
        t.failed.connect(lambda msg: (self.status.setText(msg.splitlines()[0]), self.b_cluster.setEnabled(True)))
        t.start()
        self._task = t

    def rename_person(self) -> None:
        pid = self.current_person()
        if pid is None:
            return
        row = self.store.one("SELECT name FROM persons WHERE id=?", (pid,))
        name, ok = QInputDialog.getText(self, "命名人物", "人物标签名（会成为图片标签）：",
                                        text=row["name"] or "")
        if ok and name.strip():
            self.library.name_person(pid, name.strip())
            self.changed.emit()
            self.reload()

    def merge_person(self) -> None:
        rows = self.people.selectedItems()
        if len(rows) < 2:
            QMessageBox.information(self, "合并", "请选择 2 个以上人物，第一个保留。")
            return
        ids = [int(r.data(Qt.UserRole)) for r in rows]
        for src in ids[1:]:
            self.library.merge_persons(src, ids[0])
        self.reload()

    def delete_person(self) -> None:
        pid = self.current_person()
        if pid is None:
            return
        self.store.set_face_person([int(r["id"]) for r in self.store.query("SELECT id FROM faces WHERE person_id=?", (pid,))], None)
        self.store.execute("DELETE FROM persons WHERE id=?", (pid,))
        self.reload()


# --------------------------------------------------------------------------- 系列
class SeriesDialog(QDialog):
    """把选中的图片合并成一个系列文件夹：文件夹名带标签，内部文件为页码。"""

    def __init__(self, files: list[dict], parent=None, default_name: str = "", default_tags: str = "",
                 digits: int = 3, mode: str = "copy"):
        super().__init__(parent)
        self.setWindowTitle("合并为系列")
        self.resize(760, 640)
        self.files = files
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(default_name)
        form.addRow("系列名", self.name_edit)
        self.tags_edit = QLineEdit(default_tags)
        self.tags_edit.setPlaceholderText("空格分隔，会写进文件夹名，例如：[作者 作品名 泳装]")
        form.addRow("标签", self.tags_edit)
        row = QHBoxLayout()
        self.start = QSpinBox()
        self.start.setRange(0, 9999)
        self.start.setValue(1)
        self.digits = QSpinBox()
        self.digits.setRange(1, 6)
        self.digits.setValue(digits)
        self.mode = QComboBox()
        self.mode.addItems(["复制（保留原图）", "移动（原位置删除）"])
        self.mode.setCurrentIndex(0 if mode == "copy" else 1)
        row.addWidget(QLabel("起始页码")); row.addWidget(self.start)
        row.addWidget(QLabel("页码位数")); row.addWidget(self.digits)
        row.addWidget(QLabel("方式")); row.addWidget(self.mode, 1)
        w = QWidget(); w.setLayout(row)
        form.addRow("页码", w)
        v.addLayout(form)

        v.addWidget(label("顺序（拖动排序 · 双击看大图 · F2/右键改单页文件名）", "#8f96a3"))
        # 与「调整系列顺序」共用同一个可视化排序控件（缩略图 + 拖动 + 双击看大图）
        from .common import SeriesOrderList
        self.list = SeriesOrderList()
        self.list.renameRequested.connect(self.edit_page_label)
        rows = []
        for f in files:
            if isinstance(f, dict):
                rows.append({"id": f.get("id"), "name": f.get("name") or "",
                             "path": f.get("path") or "", "mtime": f.get("mtime") or 0,
                             "page_no": 0})
            else:
                rows.append({"id": None, "name": str(f), "path": "", "mtime": 0, "page_no": 0})
        if all(r["id"] for r in rows):
            self.list.set_files(rows)
        else:                       # 没有 id/path 的老调用方式：退回纯文本列表
            for r in rows:
                it = QListWidgetItem(r["name"])
                it.setData(Qt.UserRole, r["id"])
                self.list.addItem(it)
        v.addWidget(self.list, 1)
        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        v.addWidget(self.preview)
        ok_cancel(self, v)
        self._update_preview()
        self.list.model().rowsMoved.connect(self._update_preview)
        self.name_edit.textChanged.connect(self._update_preview)
        self.start.valueChanged.connect(self._update_preview)
        self.digits.valueChanged.connect(self._update_preview)

    def edit_page_label(self, item: QListWidgetItem) -> None:
        cur = item.data(Qt.UserRole + 1) or ""
        text, ok = QInputDialog.getText(self, "手动页码", "该页文件名（不含后缀）：", text=cur)
        if ok:
            item.setData(Qt.UserRole + 1, text.strip())
            self._update_preview()

    def _page_labels(self) -> list[str]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            custom = it.data(Qt.UserRole + 1)
            out.append(custom or f"{self.start.value() + i:0{self.digits.value()}d}")
        return out

    def _update_preview(self, *_) -> None:
        name = self.name_edit.text().strip() or "(未命名)"
        from .. import tag_i18n
        tags = tag_i18n.parse_list(self.tags_edit.text())
        folder = f"{name} [{' '.join(tags)}]" if tags else name
        labels = self._page_labels()
        sample = "、".join(labels[:6]) + (" …" if len(labels) > 6 else "")
        self.preview.setText(f"目标：<b>{folder}/</b><br>共 {len(labels)} 页，文件名：{sample}")

    def values(self) -> dict:
        from .. import tag_i18n
        order, page_names = [], {}
        for i in range(self.list.count()):
            it = self.list.item(i)
            fid = it.data(Qt.UserRole)
            if fid is not None:
                order.append(int(fid))
            custom = it.data(Qt.UserRole + 1)
            if custom and fid is not None:
                page_names[int(fid)] = custom
        return {"name": self.name_edit.text().strip() or "series",
                "tags": tag_i18n.parse_list(self.tags_edit.text()),
                "order": order, "page_names": page_names,
                "digits": int(self.digits.value()), "start": int(self.start.value()),
                "mode": "copy" if self.mode.currentIndex() == 0 else "move"}


# --------------------------------------------------------------------------- 设置
class SettingsDialog(QDialog):
    # ---------- 改动即存 ----------
    # 以前必须点「确定」才写盘，一旦按钮/焦点/焦点丢失等任何环节出问题，
    # 用户看到的就是"勾了没反应、关掉就丢"。现在任何一处改动都会立刻写进 settings.json
    # （带 150ms 防抖），「确定」只是关窗口。
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_live_wired", False):
            self._live_wired = True
            self._wire_live()

    def _wire_live(self) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox
        if not hasattr(self, "_live_timer"):
            self._live_timer = QTimer(self)
            self._live_timer.setSingleShot(True)
            self._live_timer.setInterval(150)
            self._live_timer.timeout.connect(self._live_apply)

        def fire(*_a) -> None:
            self._live_timer.start()

        for w in self.findChildren(QCheckBox):
            w.toggled.connect(fire)
        for w in self.findChildren(QComboBox):
            w.currentIndexChanged.connect(fire)
        for w in self.findChildren(QSpinBox) + self.findChildren(QDoubleSpinBox):
            w.valueChanged.connect(fire)
        line_edits = [w for w in self.findChildren(QLineEdit) if w.text().strip() != ""]
        for w in line_edits:
            w.editingFinished.connect(fire)

    def _pick_splash_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "选择开屏封面图文件夹",
                                             self.splash_dir.text().strip() or "")
        if d:
            self.splash_dir.setText(d)
            self._live_apply()

    def _live_apply(self) -> None:
        try:
            self.apply_to(self.s)
            self.s.save()
        except Exception:
            pass
        # 立刻让主窗口应用（R18 打码、缩略图尺寸等）：以前要关窗口甚至重启才生效
        try:
            par = self.parent()
            if hasattr(par, "apply_settings_live"):
                par.apply_settings_live()
        except Exception:
            pass

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("设置")
        self.resize(680, 720)
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        v.addWidget(tabs, 1)

        # 常规
        w1 = QWidget(); f1 = QFormLayout(w1)
        self.models_dir = QLineEdit(settings.models_dir or str(settings.models_path()))
        btn = QPushButton("选择…")
        btn.clicked.connect(self.pick_models_dir)
        row = QHBoxLayout(); row.addWidget(self.models_dir, 1); row.addWidget(btn)
        w = QWidget(); w.setLayout(row)
        f1.addRow("模型目录", w)
        self.device = QComboBox()
        self.device.addItems(["auto（有显卡就用显卡）", "cuda", "cpu"])
        self.device.setCurrentIndex({"auto": 0, "cuda": 1, "cpu": 2}.get(settings.device, 0))
        f1.addRow("推理设备", self.device)
        self.storage = QComboBox()
        self.storage.addItems(["文件名（[tag] 写进文件名/文件夹名，手机可直接搜到）", "仅数据库（不动磁盘）"])
        self.storage.setCurrentIndex(0 if settings.tag_storage == "filename" else 1)
        f1.addRow("标签存储", self.storage)
        self.thumbs = QSpinBox(); self.thumbs.setRange(90, 420); self.thumbs.setSingleStep(10)
        self.thumbs.setValue(settings.thumb_size)
        f1.addRow("缩略图大小", self.thumbs)
        self.ignore = QLineEdit(" ".join(settings.ignore_dirs))
        f1.addRow("忽略目录", self.ignore)
        self.lib_name = QLineEdit(settings.library_dir_name)
        self.lib_name.setToolTip("每个盘下的专用图库目录名，默认 E:\\ImageTags、D:\\ImageTags …")
        f1.addRow("图库目录名", self.lib_name)
        self.lib_only = QCheckBox("检索只查图库目录（不含扫描来源）")
        self.lib_only.setChecked(settings.library_only_search)
        f1.addRow("", self.lib_only)
        self.auto_import = QCheckBox("审核完成后自动把图片收录进图库")
        self.auto_import.setChecked(settings.auto_import_after_review)
        f1.addRow("", self.auto_import)
        self.keep_orig = QCheckBox("写回文件名时保留原文件名（取消勾选 = 只用标签命名）")
        self.keep_orig.setChecked(settings.rename_keep_original)
        self.keep_orig.setToolTip("勾选：IMG_1234 [初音未来 泳装].jpg\n"
                                  "取消： [初音未来 泳装].jpg（同名自动加 _2、_3 后缀）")
        f1.addRow("", self.keep_orig)
        self.specific_only = QCheckBox("文件名里父子标签只留最具体的（有「白裙子」就不写「裙子」）")
        self.specific_only.setChecked(getattr(settings, "tag_most_specific_on_disk", True))
        self.specific_only.setToolTip("只影响写进文件名/系列文件夹名的标签；\n"
                                      "库里始终保留全部标签，检索（筛父标签会带出子标签）与模型反馈都不受影响。")
        f1.addRow("", self.specific_only)
        self.infer_parent = QCheckBox("扫描回读文件名时，按从属关系自动补回父标签")
        self.infer_parent.setChecked(getattr(settings, "infer_parent_tags", True))
        self.infer_parent.setToolTip("文件名只写了「白裙子」时，重新扫描/换机器建索引会自动把\n"
                                     "它的父标签（裙子、服装…）一起补进库里，避免丢层级。")
        f1.addRow("", self.infer_parent)
        # 图谱可调参数
        self.g_ring = QSpinBox(); self.g_ring.setRange(300, 4000); self.g_ring.setSingleStep(50)
        self.g_ring.setValue(int(getattr(settings, "graph_ring_radius", 900)))
        self.g_ring.setToolTip("图谱里第一层分类所在圆的半径：越大各中心之间越松")
        f1.addRow("图谱：中心间距", self.g_ring)
        self.g_anim = QSpinBox(); self.g_anim.setRange(0, 2000); self.g_anim.setSingleStep(40)
        self.g_anim.setValue(int(getattr(settings, "graph_anim_ms", 340)))
        self.g_anim.setToolTip("增删标签/分类后重排、以及拖拽回弹的动画时长（0 = 不动画）")
        f1.addRow("图谱：动画时长(ms)", self.g_anim)
        self.g_drag = QSpinBox(); self.g_drag.setRange(0, 1200); self.g_drag.setSingleStep(20)
        self.g_drag.setValue(int(getattr(settings, "graph_drag_limit", 240)))
        self.g_drag.setToolTip("拖拽节点最多能拉离原位多少像素（松开弹回原位）")
        f1.addRow("图谱：拖拽极限(px)", self.g_drag)
        # 开屏（启动过渡窗口）
        self.splash_on = QCheckBox("启动时显示开屏（过渡窗口）")
        self.splash_on.setChecked(bool(getattr(settings, "splash_enabled", True)))
        f1.addRow("", self.splash_on)
        self.splash_dir = QLineEdit(getattr(settings, "splash_dir", "") or "")
        self.splash_dir.setPlaceholderText("开屏封面图文件夹（每次启动随机取一张；留空用内置图）")
        b_splash = QPushButton("选择文件夹…")
        b_splash.clicked.connect(self._pick_splash_dir)
        row_sp = QHBoxLayout()
        row_sp.addWidget(self.splash_dir, 1)
        row_sp.addWidget(b_splash)
        w_sp = QWidget(); w_sp.setLayout(row_sp)
        f1.addRow("开屏封面", w_sp)
        tabs.addTab(w1, "常规")

        # WD14
        w2 = QWidget(); f2 = QFormLayout(w2)
        self.wd_enabled = QCheckBox("启用 WD14 二次元自动打标")
        self.wd_enabled.setChecked(settings.wd14_enabled)
        f2.addRow("", self.wd_enabled)
        self.wd_th = QDoubleSpinBox(); self.wd_th.setRange(0.05, 0.95); self.wd_th.setSingleStep(0.05)
        self.wd_th.setDecimals(2); self.wd_th.setValue(settings.wd14_threshold)
        f2.addRow("通用标签阈值", self.wd_th)
        self.wd_char = QDoubleSpinBox(); self.wd_char.setRange(0.3, 0.99); self.wd_char.setSingleStep(0.05)
        self.wd_char.setDecimals(2); self.wd_char.setValue(settings.wd14_char_threshold)
        f2.addRow("角色标签阈值", self.wd_char)
        self.wd_max = QSpinBox(); self.wd_max.setRange(5, 200); self.wd_max.setValue(settings.wd14_max_tags)
        f2.addRow("每张图最多标签数", self.wd_max)
        self.wd_rating = QCheckBox("保留分级标签（general/sensitive/questionable/explicit）")
        self.wd_rating.setChecked(settings.wd14_keep_rating)
        f2.addRow("", self.wd_rating)
        self.wd_model = QComboBox()
        for repo in model_lib.TAGGER_CHOICES:
            self.wd_model.addItem(f"{model_lib.MODELS[model_lib.TAGGER_CHOICES[repo][0]].label}",
                                  repo)
        idx = self.wd_model.findData(settings.wd14_model)
        self.wd_model.setCurrentIndex(idx if idx >= 0 else 0)
        self.wd_model.setToolTip("换更准的模型识别更好但更慢；首次使用需要在「模型管理」里下载（之后离线可用）")
        f2.addRow("WD14 模型", self.wd_model)
        tabs.addTab(w2, "WD14")

        # CLIP
        w3 = QWidget(); f3 = QFormLayout(w3)
        self.clip_enabled = QCheckBox("启用 CLIP 零样本（自定义标签靠它）")
        self.clip_enabled.setChecked(settings.clip_enabled)
        f3.addRow("", self.clip_enabled)
        self.clip_model = QComboBox()
        self.clip_model.addItems(["ViT-B-32", "ViT-L-14", "ViT-H-14"])
        self.clip_model.setCurrentText(settings.clip_model)
        f3.addRow("CLIP 模型", self.clip_model)
        self.clip_pretrained = QComboBox()
        self.clip_pretrained.addItems(["laion2b_s34b_b79k", "laion2b_s29b_b131k_ft", "laion2B-s32B-b82K"])
        self.clip_pretrained.setEditable(True)
        self.clip_pretrained.setCurrentText(settings.clip_pretrained)
        f3.addRow("权重名", self.clip_pretrained)
        self.clip_th = QDoubleSpinBox(); self.clip_th.setRange(0.1, 0.95); self.clip_th.setSingleStep(0.05)
        self.clip_th.setDecimals(2); self.clip_th.setValue(settings.clip_threshold)
        f3.addRow("命中阈值", self.clip_th)
        tabs.addTab(w3, "CLIP")

        # 人脸
        w4 = QWidget(); f4 = QFormLayout(w4)
        self.face_enabled = QCheckBox("启用人脸检测（真人照片→人物标签）")
        self.face_enabled.setChecked(settings.face_enabled)
        f4.addRow("", self.face_enabled)
        self.face_th = QDoubleSpinBox(); self.face_th.setRange(0.1, 0.95); self.face_th.setSingleStep(0.05)
        self.face_th.setDecimals(2); self.face_th.setValue(settings.face_det_threshold)
        f4.addRow("检测阈值", self.face_th)
        self.face_min = QSpinBox(); self.face_min.setRange(8, 300); self.face_min.setValue(settings.face_min_size)
        f4.addRow("最小人脸像素", self.face_min)
        self.face_eps = QDoubleSpinBox(); self.face_eps.setRange(0.2, 0.9); self.face_eps.setSingleStep(0.05)
        self.face_eps.setDecimals(2); self.face_eps.setValue(settings.face_cluster_eps)
        f4.addRow("聚类阈值", self.face_eps)
        tabs.addTab(w4, "人脸")

        # 查重
        w5 = QWidget(); f5 = QFormLayout(w5)
        self.dup_th = QSpinBox(); self.dup_th.setRange(0, 20)
        self.dup_th.setValue(int(settings.dup_threshold))
        f5.addRow("感知哈希差异阈值（0 最严）", self.dup_th)
        self.dup_clip = QCheckBox("再用 CLIP 特征兜一层（抓轻微裁剪/改色）")
        self.dup_clip.setChecked(settings.dup_use_clip)
        f5.addRow("", self.dup_clip)
        tabs.addTab(w5, "查重")

        # 分级
        w6 = QWidget(); f6 = QFormLayout(w6)
        self.rating_on = QCheckBox("启用分级识别（全年龄 / R15 / R18 / R18G）")
        self.rating_on.setChecked(settings.rating_enabled)
        f6.addRow("", self.rating_on)
        self.rating_confirm = QCheckBox("分级标签直接生效（不进审核队列，方便当筛选墙）")
        self.rating_confirm.setChecked(settings.rating_auto_confirm)
        f6.addRow("", self.rating_confirm)
        self.rating_blur_cb = QCheckBox("浏览时对 R18 / R18G 缩略图打码（防止公开场合尴尬）")
        self.rating_blur_cb.setChecked(settings.rating_blur)
        f6.addRow("", self.rating_blur_cb)
        note = QLabel("原理：动漫图用 WD14 自带的 4 类分级概率（general/sensitive/questionable/explicit），"
                      "真人照片用 CLIP 的“成人内容/猎奇”提示词兜底，两边融合后取等级。\n"
                      "分级标签（全年龄/R15/R18/R18G）会自动打上，也能在左侧按等级筛选。")
        note.setWordWrap(True)
        f6.addRow("", note)
        tabs.addTab(w6, "分级")

        # 性能挡位
        w7 = QWidget()
        f7 = QVBoxLayout(w7)
        f7.addWidget(QLabel("选一个挡位，保存后立即生效（会重新加载模型）："))
        from .. import perf as perf_mod
        self.perf_group = QButtonGroup(self)
        self.perf_radios: dict[str, QRadioButton] = {}
        for key in perf_mod.ORDER:
            p = perf_mod.PRESETS[key]
            rb = QRadioButton(f"{p['label']}")
            rb.setChecked(settings.perf_mode == key or (settings.perf_mode not in perf_mod.PRESETS
                                                        and key == "balanced"))
            f7.addWidget(rb)
            desc = QLabel("　　" + p["desc"])
            desc.setWordWrap(True)
            desc.setStyleSheet("color:#8f96a3;")
            f7.addWidget(desc)
            self.perf_group.addButton(rb)
            self.perf_radios[key] = rb
        note = QLabel("当前生效：" + perf_mod.current()["label"] +
                      "（改挡位后正在跑的任务会在下一个批次按新挡位执行）")
        note.setWordWrap(True)
        f7.addWidget(note)
        f7.addStretch(1)
        tabs.addTab(w7, "性能")

        ok_cancel(self, v)

    def pick_models_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择模型目录", self.models_dir.text())
        if d:
            self.models_dir.setText(d)

    def apply_to(self, settings) -> None:
        settings.models_dir = self.models_dir.text().strip()
        settings.device = ["auto", "cuda", "cpu"][self.device.currentIndex()]
        settings.tag_storage = "filename" if self.storage.currentIndex() == 0 else "db"
        settings.thumb_size = int(self.thumbs.value())
        settings.ignore_dirs = [x for x in self.ignore.text().split() if x]
        settings.wd14_enabled = self.wd_enabled.isChecked()
        settings.wd14_threshold = float(self.wd_th.value())
        settings.wd14_char_threshold = float(self.wd_char.value())
        settings.wd14_max_tags = int(self.wd_max.value())
        settings.wd14_keep_rating = self.wd_rating.isChecked()
        settings.wd14_model = self.wd_model.currentData() or settings.wd14_model
        settings.clip_enabled = self.clip_enabled.isChecked()
        settings.clip_model = self.clip_model.currentText()
        settings.clip_pretrained = self.clip_pretrained.currentText()
        settings.clip_threshold = float(self.clip_th.value())
        settings.face_enabled = self.face_enabled.isChecked()
        settings.face_det_threshold = float(self.face_th.value())
        settings.face_min_size = int(self.face_min.value())
        settings.face_cluster_eps = float(self.face_eps.value())
        settings.library_dir_name = self.lib_name.text().strip() or "ImageTags"
        settings.library_only_search = self.lib_only.isChecked()
        settings.auto_import_after_review = self.auto_import.isChecked()
        settings.rename_keep_original = self.keep_orig.isChecked()
        settings.tag_most_specific_on_disk = self.specific_only.isChecked()
        settings.infer_parent_tags = self.infer_parent.isChecked()
        settings.graph_ring_radius = float(self.g_ring.value())
        settings.graph_anim_ms = int(self.g_anim.value())
        settings.graph_drag_limit = float(self.g_drag.value())
        try:
            settings.splash_enabled = bool(self.splash_on.isChecked())
            settings.splash_dir = self.splash_dir.text().strip()
        except Exception:
            pass
        settings.dup_threshold = int(self.dup_th.value())
        settings.dup_use_clip = self.dup_clip.isChecked()
        settings.rating_enabled = self.rating_on.isChecked()
        settings.rating_auto_confirm = self.rating_confirm.isChecked()
        settings.rating_blur = self.rating_blur_cb.isChecked()
        from .. import perf as perf_mod
        for key, rb in self.perf_radios.items():
            if rb.isChecked():
                settings.perf_mode = key
                break


# --------------------------------------------------------------------------- 预览/框选
class ImageCanvas(QWidget):
    """大图显示 + 可选拖框标注（坐标按比例存储，任何分辨率都能对齐）。"""

    regionDrawn = Signal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pm: QPixmap | None = None
        self.zoom = 1.0
        self.annotate = False
        self.highlight: str | None = None      # 高亮某个标签对应的框（审核/预览用）
        self.regions: list[tuple[str, float, float, float, float]] = []
        self._start: QPoint | None = None
        self._cur: QPoint | None = None
        self.pan = QPointF(0, 0)              # 放大后可拖动平移的偏移量
        self._panning = False
        self._pan_from: QPoint | None = None
        self._pan_origin = QPointF(0, 0)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)

    def set_pixmap(self, pm: QPixmap) -> None:
        self.pm = pm
        self.zoom = 1.0
        self.pan = QPointF(0, 0)              # 换图时复位平移
        self.updateGeometry()
        self.update()

    def _target_rect(self) -> QRectF:
        if self.pm is None:
            return QRectF()
        avail = self.size()
        pw, ph = self.pm.width(), self.pm.height()
        if pw <= 0 or ph <= 0 or avail.width() <= 0 or avail.height() <= 0:
            return QRectF()      # 空图/还没布局时别除零（否则大图区直接画不出来）
        scale = min(avail.width() / pw, avail.height() / ph) * self.zoom
        scale = max(scale, 0.02)
        w, h = pw * scale, ph * scale
        return QRectF((avail.width() - w) / 2 + self.pan.x(),
                      (avail.height() - h) / 2 + self.pan.y(), w, h)

    def mousePressEvent(self, e) -> None:
        if not self.annotate or self.pm is None:
            # 非标注模式 = 拖动平移（放大后想看别处不用来回滚缩放）
            if self.pm is not None and e.button() == Qt.LeftButton:
                self._panning = True
                self._pan_from = e.position().toPoint()
                self._pan_origin = QPointF(self.pan)
                self.setCursor(Qt.ClosedHandCursor)
            return
        r = self._target_rect()
        if r.contains(e.position()):
            self._start = e.position().toPoint()
            self._cur = self._start
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._panning and self._pan_from is not None:
            d = e.position().toPoint() - self._pan_from
            self.pan = self._pan_origin + QPointF(d.x(), d.y())
            self.update()
            return
        if self._start is not None:
            self._cur = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._panning:
            self._panning = False
            self._pan_from = None
            self.setCursor(Qt.ArrowCursor)
            return
        if self._start is None or self.pm is None:
            return
        end = e.position().toPoint()
        r = self._target_rect()
        rect = QRect(self._start, end).normalized()
        self._start = self._cur = None
        if rect.width() < 6 or rect.height() < 6 or not r.contains(rect.center()):
            self.update()
            return
        x = (rect.left() - r.left()) / r.width()
        y = (rect.top() - r.top()) / r.height()
        w = rect.width() / r.width()
        h = rect.height() / r.height()
        self.regionDrawn.emit(max(0.0, x), max(0.0, y), min(1.0, w), min(1.0, h))
        self.update()

    def wheelEvent(self, e) -> None:
        # 以**鼠标位置**为中心缩放：先记下光标下面是图的哪一点，缩放后把这一点挪回光标处
        pos = e.position()
        before = self._target_rect()
        self.zoom = max(0.2, min(8.0, self.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1)))
        if before.width() > 0 and before.height() > 0:
            ux = (pos.x() - before.left()) / before.width()
            uy = (pos.y() - before.top()) / before.height()
            after = self._target_rect()          # 新 zoom + 旧 pan
            self.pan += QPointF(pos.x() - (after.left() + ux * after.width()),
                                pos.y() - (after.top() + uy * after.height()))
        self.update()

    def fit_view(self) -> None:
        """适应窗口：缩放回 1.0 并居中（和"换图"时的初始状态一致）。"""
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#141519"))
        if self.pm is None:
            p.setPen(QColor("#8f96a3"))
            p.drawText(self.rect(), Qt.AlignCenter, "（无图片）")
            return
        r = self._target_rect()
        p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
        f = QFont(); f.setPointSizeF(9); p.setFont(f)
        for name, x, y, w, h in self.regions:
            hot = self.highlight is not None and name == self.highlight
            if self.highlight is not None and not hot:
                pen = QPen(QColor(255, 92, 138, 90), 1)
            elif hot:
                pen = QPen(QColor("#ffd54a"), 3)
            else:
                pen = QPen(QColor("#ff5c8a"), 2)
            p.setPen(pen)
            box = QRectF(r.left() + x * r.width(), r.top() + y * r.height(), w * r.width(), h * r.height())
            p.drawRect(box)
            if self.highlight is not None and not hot:
                continue
            tw = p.fontMetrics().horizontalAdvance(name) + 8
            bg = QColor(255, 213, 74, 220) if hot else QColor(255, 92, 138, 200)
            p.fillRect(QRectF(box.left(), max(r.top(), box.top() - 16), tw, 16), bg)
            p.setPen(QColor("#101014"))
            p.drawText(QRectF(box.left() + 4, max(r.top(), box.top() - 16), tw, 16), Qt.AlignVCenter, name)
        if self._start and self._cur:
            p.setPen(QPen(QColor("#7fd0ff"), 2, Qt.DashLine))
            p.drawRect(QRect(self._start, self._cur).normalized())


class SeriesOrderDialog(QDialog):
    """系列排序：缩略图列表直接拖动改顺序（保存后按新顺序重命名页码），双击看大图。"""

    def __init__(self, store, series_id: int, parent=None):
        super().__init__(parent)
        self.store = store
        self.series_id = int(series_id)
        s = store.one("SELECT name FROM series WHERE id=?", (self.series_id,))
        self.setWindowTitle(f"调整系列顺序 — {s['name'] if s else ''}")
        self.resize(820, 620)
        from .common import ThumbPool, load_pixmap
        self._load_pixmap = load_pixmap
        from .common import SeriesOrderList
        v = QVBoxLayout(self)
        tip = QLabel("拖动缩略图调整页面顺序；双击看大图。点「保存顺序」后按新顺序重命名页码（原地改名，不移动文件）。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8f96a3;")
        v.addWidget(tip)
        # 和「合并为系列」共用同一个可视化排序控件（缩略图 + 拖动 + 双击看大图）
        self.list = SeriesOrderList()
        v.addWidget(self.list, 1)
        self.thumbs = ThumbPool(size=160)
        self.thumbs.signals.ready.connect(self._on_thumb)
        self._items: dict[int, QListWidgetItem] = {}
        self.reload()
        ok_cancel(self, v)
        try:
            self.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).setText("保存顺序")
        except Exception:
            pass

    def reload(self) -> None:
        self.list.set_files(self.store.series_files(self.series_id))

    def _on_thumb(self, file_id: int, path: str) -> None:
        it = self._items.get(int(file_id))
        if it is None or not path:
            return
        pm = self._load_pixmap(path)
        if not pm.isNull():
            it.setIcon(QIcon(pm.scaled(140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation)))

    def _preview(self, item) -> None:
        fid = int(item.data(Qt.UserRole) or 0)
        row = self.store.one("SELECT path FROM files WHERE id=?", (fid,))
        if row is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(Path(row["path"]).name)
        dlg.resize(900, 700)
        lay = QVBoxLayout(dlg)
        lab = QLabel()
        lab.setAlignment(Qt.AlignCenter)
        pm = self._load_pixmap(row["path"])
        lab.setPixmap(pm.scaled(860, 640, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(lab, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("关闭")
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def ordered_ids(self) -> list[int]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            fid = it.data(Qt.UserRole)
            if fid:
                out.append(int(fid))
        return out


class PreviewDialog(QDialog):
    """大图预览 + 标签编辑 + 手动框选标注（可用但不必用）。"""

    tagsChanged = Signal(int)

    def __init__(self, store, path: str, parent=None, models_dir=None, browse_ids=None):
        super().__init__(parent)
        self.store = store
        # 浏览模式：带着"当前这批图"的 id 列表进来，就能 ↑/↓ 翻页
        self._ids: list[int] = [int(i) for i in (browse_ids or [])]
        self.setWindowTitle(Path(path).name)
        self.resize(1180, 780)
        self.path = path
        row = self.store.one("SELECT * FROM files WHERE path=?", (str(Path(path).resolve()),))
        if row is None:
            row = self.store.file_by_path(path)
        self.file_id = int(row["id"]) if row else None
        v = QVBoxLayout(self)
        # ---- 翻页条（浏览模式）：上一张 / 第 n/N 张 / 下一张 ----
        nav = QHBoxLayout()
        self.b_prev = QPushButton("← 上一张")
        self.b_prev.clicked.connect(lambda: self.goto(self._idx - 1))
        self.b_next = QPushButton("下一张 →")
        self.b_next.clicked.connect(lambda: self.goto(self._idx + 1))
        self.nav_label = QLabel("")
        self.nav_label.setStyleSheet("color:#8f96a3;")
        nav.addWidget(self.b_prev)
        nav.addWidget(self.b_next)
        nav.addWidget(self.nav_label)
        nav.addStretch(1)
        nav.addWidget(QLabel("← / → 翻页 · Esc 关闭 · 滚轮以鼠标为中心缩放 · 左键拖动平移"))
        v.addLayout(nav)
        bar = QHBoxLayout()
        self.b_annotate = QPushButton("框选标注：关")
        self.b_annotate.setCheckable(True)
        self.b_annotate.toggled.connect(self.toggle_annotate)
        bar.addWidget(self.b_annotate)
        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        self.tag_combo.setInsertPolicy(QComboBox.NoInsert)
        self.tag_combo.setMinimumWidth(200)
        bar.addWidget(QLabel("框选对应的标签"))
        bar.addWidget(self.tag_combo, 1)
        self.b_existing = QPushButton("新增/管理标签…")
        self.b_existing.clicked.connect(self.quick_add_tag)
        bar.addWidget(self.b_existing)
        self.also_file = QCheckBox("同时给整图打上该标签")
        self.also_file.setChecked(True)
        bar.addWidget(self.also_file)
        v.addLayout(bar)
        self.tip = QLabel("框选的作用是告诉模型「这个标签对应画面哪一块」：框内区域会单独算特征，"
                          "训练出该标签的“区域中心”，以后眼镜、领带这类只占一小块的标签会更准。"
                          "框选完全可选，不框也能用。")
        self.tip.setWordWrap(True)
        self.tip.setStyleSheet("color:#8f96a3;")
        self.tip.setVisible(False)          # 浏览模式默认只留"图 + 标签"，框选相关收起来
        v.addWidget(self.tip)

        split = QSplitter(Qt.Horizontal)
        self.canvas = ImageCanvas()
        self.canvas.regionDrawn.connect(self.on_region_drawn)
        # 大图操作条：适应窗口 / 100% / 提示
        ops = QHBoxLayout()
        b_fit = QPushButton("适应窗口")
        b_fit.setToolTip("缩放回 1.0 并居中（左键拖动可平移，滚轮以鼠标位置为中心缩放）")
        b_fit.clicked.connect(self.canvas.fit_view)
        ops.addWidget(b_fit)
        b_zoom_in = QPushButton("放大 +")
        b_zoom_in.clicked.connect(lambda: setattr(self.canvas, "zoom", min(8.0, self.canvas.zoom * 1.25)) or self.canvas.update())
        ops.addWidget(b_zoom_in)
        b_zoom_out = QPushButton("缩小 −")
        b_zoom_out.clicked.connect(lambda: setattr(self.canvas, "zoom", max(0.2, self.canvas.zoom / 1.25)) or self.canvas.update())
        ops.addWidget(b_zoom_out)
        ops.addWidget(QLabel("左键拖动平移 · 滚轮以鼠标位置缩放"))
        ops.addStretch(1)
        v.addLayout(ops)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        split.addWidget(scroll)
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(label("整图标签", "#7fd0ff", True))
        self.tag_list = QListWidget()
        self.tag_list.itemSelectionChanged.connect(self.highlight_tag_regions)
        pv.addWidget(self.tag_list, 1)
        add_row = QHBoxLayout()
        self.new_tag = QLineEdit()
        self.new_tag.setPlaceholderText("输入标签后回车添加")
        # 这里以前完全没有联想（双击图片进来的标签页就是它）——接到统一的联想引擎上
        try:
            from .common import install_tag_suggest
            install_tag_suggest(self.new_tag, self.store)
        except Exception:
            pass
        self.new_tag.returnPressed.connect(self.add_whole_image_tag)
        add_row.addWidget(self.new_tag, 1)
        b_add = QPushButton("添加")
        b_add.clicked.connect(self.add_whole_image_tag)
        add_row.addWidget(b_add)
        pv.addLayout(add_row)
        b_rm = QPushButton("删除选中标签")
        b_rm.clicked.connect(self.remove_selected_tag)
        pv.addWidget(b_rm)
        self.region_box = QWidget()
        rb = QVBoxLayout(self.region_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.addWidget(label("框选区域", "#ff5c8a", True))
        self.region_list = QListWidget()
        self.region_list.itemDoubleClicked.connect(self.edit_region)
        rb.addWidget(self.region_list, 1)
        b_rm_r = QPushButton("删除选中框")
        b_rm_r.clicked.connect(self.remove_region)
        rb.addWidget(b_rm_r)
        self.region_box.setVisible(False)
        pv.addWidget(self.region_box, 1)
        split.addWidget(panel)
        split.setSizes([860, 320])
        v.addWidget(split, 1)
        self._idx = self._ids.index(self.file_id) if (self.file_id in self._ids) else -1
        self._sync_nav()
        self.load()

    # ---- 浏览模式：翻页 ----
    def _sync_nav(self) -> None:
        n = len(self._ids)
        if n and self._idx >= 0:
            self.nav_label.setText(f"第 {self._idx + 1}/{n} 张")
        else:
            self.nav_label.setText("单张浏览")
        self.b_prev.setEnabled(self._idx > 0)
        self.b_next.setEnabled(0 <= self._idx < max(0, n - 1))

    def goto(self, i: int) -> None:
        """翻到当前这批图的第 i 张（保留缩放/平移能力，换图自动适应窗口）。"""
        if not self._ids:
            return
        i = max(0, min(int(i), len(self._ids) - 1))
        if i == self._idx:
            return
        fid = self._ids[i]
        row = self.store.one("SELECT path FROM files WHERE id=?", (fid,))
        if row is None:
            return
        self._idx = i
        self.file_id = int(fid)
        self.path = row["path"]
        self.setWindowTitle(Path(self.path).name)
        self.canvas.fit_view()
        self.load()
        self._sync_nav()

    def keyPressEvent(self, ev) -> None:
        k = ev.key()
        if k in (Qt.Key_Left, Qt.Key_PageUp, Qt.Key_Up):
            self.goto(self._idx - 1)
            ev.accept()
            return
        if k in (Qt.Key_Right, Qt.Key_PageDown, Qt.Key_Down, Qt.Key_Space):
            self.goto(self._idx + 1)
            ev.accept()
            return
        if k == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(ev)

    # ---- 载入 ----
    def load(self) -> None:
        self.canvas.set_pixmap(load_pixmap(self.path))
        self.reload_tags()
        self.reload_regions()
        self.reload_tag_combo()

    def reload_tag_combo(self) -> None:
        self.tag_combo.clear()
        for t in self.store.list_tags():
            self.tag_combo.addItem(f"{t['name']}  ({TAG_CATEGORIES.get(t['category'], t['category'])})", t["name"])

    def reload_tags(self) -> None:
        self.tag_list.clear()
        if self.file_id is None:
            return
        for t in self.store.tags_for_file(self.file_id):
            src = SOURCE_LABELS.get(t["source"], t["source"])
            score = f"{t['score']:.2f}" if t["source"] != "manual" else ""
            it = QListWidgetItem(f"{t['name']}   [{src}{(' ' + score) if score else ''}]")
            it.setData(Qt.UserRole, t["name"])
            self.tag_list.addItem(it)

    def reload_regions(self) -> None:
        self.region_list.clear()
        self.canvas.regions = []
        if self.file_id is None:
            return
        for r in self.store.regions_for_file(self.file_id):
            rect = (r["x"], r["y"], r["w"], r["h"])
            self.canvas.regions.append((r["tag_name"] or "", *rect))
            it = QListWidgetItem(f"{r['tag_name']}")
            it.setData(Qt.UserRole, int(r["id"]))
            self.region_list.addItem(it)
        self.canvas.update()

    def highlight_tag_regions(self) -> None:
        """在左侧标签表里选中某个标签 → 大图高亮它对应的框（方便核对偏差）。"""
        it = self.tag_list.currentItem()
        self.canvas.highlight = it.data(Qt.UserRole) if it else None
        self.canvas.update()

    # ---- 标签 ----
    def add_whole_image_tag(self) -> None:
        name = self.new_tag.text().strip()
        if not name or self.file_id is None:
            return
        self.store.add_file_tags(self.file_id, [(name, "manual", 1.0)])
        self.new_tag.clear()
        self.reload_tags()
        self.tagsChanged.emit(self.file_id)

    def remove_selected_tag(self) -> None:
        it = self.tag_list.currentItem()
        if not it or self.file_id is None:
            return
        self.store.remove_file_tags(self.file_id, [it.data(Qt.UserRole)])
        self.reload_tags()
        self.tagsChanged.emit(self.file_id)

    def quick_add_tag(self) -> None:
        dlg = TagEditDialog(self, self.tag_combo.currentText().split("  (")[0])
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            if v["name"]:
                self.store.save_tag(v["name"], v["category"], v["prompt"], v["auto"],
                                    v.get("requires", ""))
                self.reload_tag_combo()
                self.tag_combo.setCurrentText(v["name"])

    # ---- 框选 ----
    def toggle_annotate(self, on: bool) -> None:
        self.canvas.annotate = on
        self.b_annotate.setText("框选标注：开" if on else "框选标注：关")
        self.canvas.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        # 只有开了框选才显示相关控件：平时保持"只有图 + 标签"的干净浏览界面
        for w in (getattr(self, "tip", None), getattr(self, "region_box", None)):
            if w is not None:
                w.setVisible(bool(on))

    def on_region_drawn(self, x: float, y: float, w: float, h: float) -> None:
        if self.file_id is None:
            return
        name = self.tag_combo.currentText().split("  (")[0].strip()
        if not name:
            QMessageBox.information(self, "框选", "先在右上角选择或输入一个标签。")
            return
        cat = "other"
        row = self.store.one("SELECT category FROM tags WHERE name=?", (name,))
        if row:
            cat = row["category"]
        self.store.add_region(self.file_id, name, (x, y, w, h), category=cat)
        if self.also_file.isChecked():
            self.store.add_file_tags(self.file_id, [(name, "manual", 1.0)])
            self.reload_tags()
        self.reload_regions()
        self.tagsChanged.emit(self.file_id)

    def edit_region(self) -> None:
        it = self.region_list.currentItem()
        if not it:
            return
        rid = int(it.data(Qt.UserRole))
        row = self.store.one("SELECT * FROM regions WHERE id=?", (rid,))
        if not row:
            return
        text, ok = QInputDialog.getText(self, "修改框选标签", "标签名：", text=row["tag_name"] or "")
        if ok and text.strip():
            self.store.update_region(rid, tag_name=text.strip())
            self.reload_regions()

    def remove_region(self) -> None:
        it = self.region_list.currentItem()
        if not it:
            return
        self.store.delete_region(int(it.data(Qt.UserRole)))
        self.reload_regions()


# --------------------------------------------------------------------------- 模型
class ModelsDialog(QDialog):
    """模型下载/状态。首次使用前点一下"下载缺失模型"即可。"""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("模型管理")
        self.resize(720, 420)
        v = QVBoxLayout(self)
        v.addWidget(label("所有模型都从国内镜像 hf-mirror.com 下载，下载后完全离线运行。", "#8f96a3"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["模型", "用途", "大小", "状态"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(1, 200)
        v.addWidget(self.table, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        v.addWidget(self.bar)
        self.status = QLabel("")
        v.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        b_missing = QPushButton("下载缺失模型")
        b_missing.clicked.connect(self.download_missing)
        row.addWidget(b_missing)
        b_all = QPushButton("下载/校验全部")
        b_all.clicked.connect(self.download_all)
        row.addWidget(b_all)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        v.addLayout(row)
        self.reload()

    def reload(self) -> None:
        md = self.settings.models_path()
        keys = ["wd14_onnx", "wd14_tags", "face_det", "face_rec", "clip"]
        self.table.setRowCount(len(keys))
        for i, k in enumerate(keys):
            spec = model_lib.MODELS[k]
            self.table.setItem(i, 0, QTableWidgetItem(spec.label))
            self.table.setItem(i, 1, QTableWidgetItem(spec.dest))
            self.table.setItem(i, 2, QTableWidgetItem(f"{spec.approx_mb} MB"))
            ok = model_lib.is_present(k, md)
            it = QTableWidgetItem("已就绪" if ok else "缺失")
            it.setForeground(QColor("#7ddc7d") if ok else QColor("#ff8a8a"))
            self.table.setItem(i, 3, it)

    def _run(self, keys) -> None:
        self.status.setText("开始下载…")
        md = self.settings.models_path()

        def job(progress, cancel, item):
            for k in keys:
                model_lib.ensure(k, md, progress)
            return True

        t = Task(job, self, "模型下载")
        t.progress.connect(lambda msg, frac: (self.status.setText(msg), self.bar.setValue(int(frac * 100) if frac >= 0 else 0)))
        t.done.connect(lambda _r: (self.status.setText("完成"), self.bar.setValue(100), self.reload()))
        t.failed.connect(lambda msg: self.status.setText(msg.splitlines()[0]))
        t.start()
        self._task = t

    def download_missing(self) -> None:
        md = self.settings.models_path()
        keys = [k for k in ("wd14_onnx", "wd14_tags", "face_det", "face_rec") if not model_lib.is_present(k, md)]
        if not keys:
            QMessageBox.information(self, "模型", "基础模型都已就绪。")
            return
        self._run(keys)

    def download_all(self) -> None:
        self._run(["wd14_onnx", "wd14_tags", "face_det", "face_rec"])
