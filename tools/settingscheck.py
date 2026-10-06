"""逐项验证设置窗口里**每一个控件**是否真的会写盘（不再靠人肉一个个试）。

为什么要它：之前我只是 grep 了"设置对象有没有多份引用"，却没验证"点一下控件会不会落盘"，
于是每出一个问题都得用户报一次。这个脚本把设置窗口里所有复选框/下拉/数字框自动改一遍，
每改一个就读一次 settings.json，看字段有没有真的变，最后打一张表。

用法： python tools\\settingscheck.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def main() -> int:
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox

    app = QApplication([])
    from app.config import Settings
    from app.ui.dialogs import SettingsDialog

    p = Settings.path()
    backup = p.read_text(encoding="utf-8") if p.exists() else None
    try:
        s = Settings.load()
        dlg = SettingsDialog(s)
        dlg.resize(700, 780)
        dlg.show()
        for _ in range(4):
            app.processEvents()

        def disk() -> dict:
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}

        def settle(sec: float = 0.45) -> None:
            t0 = time.time()
            while time.time() - t0 < sec:
                app.processEvents()
                time.sleep(0.02)

        rows: list[tuple[str, str, str, str]] = []
        widgets: list = []
        widgets += dlg.findChildren(QCheckBox)
        widgets += dlg.findChildren(QComboBox)
        widgets += dlg.findChildren(QSpinBox) + dlg.findChildren(QDoubleSpinBox)

        for w in widgets:
            if not w.isEnabled():
                continue
            label = (w.text() if isinstance(w, QCheckBox) else w.objectName()) or ""
            label = (label or w.toolTip() or type(w).__name__)[:34]
            before = disk()
            try:
                if isinstance(w, QCheckBox):
                    w.setChecked(not w.isChecked())      # 用键盘等价方式（setChecked 与点击同属控件状态变更）
                elif isinstance(w, QComboBox):
                    w.setCurrentIndex((w.currentIndex() + 1) % max(1, w.count()))
                else:
                    w.setValue(w.value() + (1 if isinstance(w, QSpinBox) else 0.05))
            except Exception as exc:
                rows.append((type(w).__name__, label, "无法改动", str(exc)[:30]))
                continue
            settle()
            after = disk()
            diff = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
            rows.append((type(w).__name__, label,
                         "写盘 OK" if diff else "★没写盘",
                         ",".join(diff[:3])))

        print("%-14s %-36s %-8s %s" % ("控件类型", "控件", "结果", "变化的字段"))
        print("-" * 96)
        bad = 0
        for r in rows:
            if "★" in r[2]:
                bad += 1
            print("%-14s %-36s %-8s %s" % r)
        print("-" * 96)
        print(f"共 {len(rows)} 个控件，{bad} 个没有写盘")
        return 1 if bad else 0
    finally:
        if backup is not None:
            p.write_text(backup, encoding="utf-8")
            print("（已还原原 settings.json）")


if __name__ == "__main__":
    raise SystemExit(main())
