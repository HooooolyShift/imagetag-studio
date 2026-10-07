"""局域网设备发现（PC 侧）。

给平板/手机端"自动查找设备、选中即连接"用：本机在局域网里定时广播自己的存在，
同时监听别人的广播，于是双方都能列出对方。只需要 UDP，不依赖任何第三方库。

协议（极简，正式 API 之后再加 HTTP 部分）：
  · 广播/应答载荷都是 UTF-8 的 JSON，字段：app / role / name / version / port / code
  · app 必须是 "imagetag"（过滤掉别的软件广播）
  · 收到 `{"app":"imagetag","query":"who"}` 就单播回一条自身信息（用于"选中即连"前的探测）

线程模型：全部跑在 Qt 主线程的定时器里（非阻塞 socket），不另开线程，避免退出时卡住。
"""
from __future__ import annotations

import json
import random
import socket
import time

from PySide6.QtCore import QObject, QTimer, Signal

DISCOVERY_PORT = 47823
APP_KEY = "imagetag"


def local_ip() -> str:
    """取本机在局域网里的地址（不实际发包，靠路由表挑一个出口地址）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


class LanService(QObject):
    """设备发现服务：广播自己 + 收集别人。"""

    changed = Signal()          # 设备列表有变化

    def __init__(self, name: str, role: str = "pc", version: str = "", parent=None):
        super().__init__(parent)
        self.name = name
        self.role = role
        self.version = version
        self.pairing_code = f"{random.randint(0, 9999):04d}"
        self._sock: socket.socket | None = None
        self._timer: QTimer | None = None
        self._peers: dict[str, dict] = {}
        self._last_broadcast = 0.0

    # ---------- 生命周期 ----------
    def start(self, port: int = DISCOVERY_PORT) -> bool:
        if self._sock is not None:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except Exception:
                pass
            s.bind(("", port))
            s.setblocking(False)
            self._sock = s
        except Exception:
            self._sock = None
            return False
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        return True

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # ---------- 对外 ----------
    @property
    def port(self) -> int:
        return DISCOVERY_PORT

    def peers(self, alive_within: float = 8.0) -> list[dict]:
        """还在线的其它设备（8 秒内收到过广播）。"""
        now = time.time()
        out = []
        for ip, info in list(self._peers.items()):
            if now - float(info.get("seen", 0)) > alive_within:
                continue
            out.append({**info, "ip": ip})
        out.sort(key=lambda d: (-float(d.get("seen", 0)), str(d.get("name", ""))))
        return out

    def self_info(self) -> dict:
        return {"app": APP_KEY, "role": self.role, "name": self.name,
                "version": self.version, "ip": local_ip(), "port": self.port,
                "code": self.pairing_code}

    # ---------- 内部 ----------
    def _payload(self) -> bytes:
        return json.dumps(self.self_info(), ensure_ascii=False).encode("utf-8")

    def _broadcast(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendto(self._payload(), ("255.255.255.255", self.port))
        except Exception:
            pass

    def _tick(self) -> None:
        if self._sock is None:
            return
        now = time.time()
        if now - self._last_broadcast > 3.0:
            self._last_broadcast = now
            self._broadcast()
        changed = False
        me = local_ip()
        for _ in range(50):                     # 一次最多处理 50 个包，别把 UI 卡住
            try:
                data, addr = self._sock.recvfrom(4096)
            except Exception:
                break
            ip = addr[0]
            try:
                info = json.loads(data.decode("utf-8", "ignore"))
            except Exception:
                continue
            if not isinstance(info, dict) or info.get("app") != APP_KEY:
                continue
            if str(info.get("query", "")) == "who":      # 有人找设备 → 回一条自身信息
                try:
                    self._sock.sendto(self._payload(), (ip, addr[1]))
                except Exception:
                    pass
                continue
            if ip in (me, "127.0.0.1"):
                continue
            info["seen"] = now
            old = self._peers.get(ip)
            if old is None or old.get("name") != info.get("name") or old.get("role") != info.get("role"):
                changed = True
            self._peers[ip] = info
        # 掉线的也要通知一次，界面才能及时去掉
        alive = {ip for ip, v in self._peers.items() if now - float(v.get("seen", 0)) <= 8.0}
        if alive != set(self._peers):
            changed = True
        if changed:
            self.changed.emit()
