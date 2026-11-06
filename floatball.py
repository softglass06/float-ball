# -*- coding: utf-8 -*-
"""
AI 悬浮球

双击启动后，屏幕左下角出现一个悬浮球。把 .py 文件拖到球上，
程序会调用本机 `claude -p`（自动沿用 ccswitch 当前选中的供应商/额度），
让 AI 读懂文件里以注释 / docstring / 伪代码描述的需求，生成满足需求的
完整可运行代码，并以「原名_修改版.py」保存到原文件同一目录，不改动原文件。

依赖：PySide6；本机需已安装并配置好 Claude Code（含 ccswitch）。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QThread, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QMouseEvent,
    QPainter,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QMenu, QToolTip, QWidget

BALL_SIZE = 84
CLAUDE_TIMEOUT = 600  # 秒，生成代码可能较慢
OUTPUT_SUFFIX = "_修改版"

SYSTEM_PROMPT = """\
你是一名资深 Python 工程师。下面给你一个 .py 文件的内容，其中以注释、docstring 或伪代码的形式描述了需求。

请仔细阅读并理解其中的全部需求，然后写出【满足需求的、完整且可直接运行的 Python 代码】。

严格要求：
1. 只输出 Python 代码本身，第一行就是代码，不要输出任何解释、说明或客套话。
2. 不要使用 markdown 代码块围栏（即不要出现 ``` 这类符号），直接输出纯代码。
3. 代码必须完整，保存成 .py 后可以直接运行。

文件内容如下：
<<<FILE_START>>>
{content}
<<<FILE_END>>>
"""


def read_py_text(path: str) -> str:
    """读取 .py 文件文本，兼容 UTF-8(带/不带 BOM) 与 GBK 编码。"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _decode_stdout(raw: bytes) -> str:
    """claude 在本机可能按 GBK（控制台代码页）输出，先试 UTF-8 再试 GBK。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        i, j = text.find("{"), text.rfind("}")
        if i == -1 or j == -1:
            raise RuntimeError("无法解析 claude 输出：" + text[:300])
        return json.loads(text[i:j + 1])


def call_claude(prompt: str) -> str:
    """调用本机 claude -p，返回最终文本（result 字段）。"""
    # Windows 下 claude 是 .cmd 批处理脚本，必须 shell=True 才能被 cmd.exe 解析到。
    cmd = "claude -p --output-format json"
    try:
        proc = subprocess.run(
            cmd,
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=CLAUDE_TIMEOUT,
            shell=True,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude 生成超时（>{CLAUDE_TIMEOUT}s）。")

    if proc.returncode != 0:
        err = _decode_stdout(proc.stderr).strip()
        if any(k in err for k in ("不是内部或外部命令", "不是内部", "not recognized", "command not found")):
            raise RuntimeError("未找到 claude 命令。请确认已安装 Claude Code，并已配置 ccswitch。")
        raise RuntimeError(f"claude 调用失败（退出码 {proc.returncode}）：{err}")

    # 答案在 stdout；unrecognized_model 之类的告警在 stderr，这里只解析 stdout。
    data = _parse_json(_decode_stdout(proc.stdout).strip())
    if data.get("is_error"):
        raise RuntimeError("claude 返回错误：" + str(data.get("result", ""))[:500])
    return data.get("result") or data.get("message") or ""


def extract_code(text: str) -> str:
    """从模型输出中剥离可能的 markdown 围栏，得到纯代码。"""
    t = text.strip()
    if not t:
        raise RuntimeError("claude 没有返回内容。")
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    code = "\n".join(lines).strip()
    if not code:
        raise RuntimeError("生成结果为空。")
    return code + "\n"


def output_path(src: str) -> Path:
    p = Path(src)
    return p.with_name(p.stem + OUTPUT_SUFFIX + p.suffix)


def process_one(path: str) -> Path:
    content = read_py_text(path)
    prompt = SYSTEM_PROMPT.format(content=content)
    text = call_claude(prompt)
    code = extract_code(text)
    out = output_path(path)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    return out


class Worker(QThread):
    """后台线程，逐个处理文件，不阻塞悬浮球 UI。"""

    file_started = Signal(str)       # 源文件路径
    file_done = Signal(str, str)     # 源文件路径, 生成文件路径
    file_failed = Signal(str, str)   # 源文件路径, 错误信息
    all_finished = Signal()

    def __init__(self, files, parent=None):
        super().__init__(parent)
        self.files = list(files)

    def run(self):
        for f in self.files:
            self.file_started.emit(f)
            try:
                out = process_one(f)
                self.file_done.emit(f, str(out))
            except Exception as e:  # noqa: BLE001
                self.file_failed.emit(f, str(e))
        self.all_finished.emit()


class FloatingBall(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(BALL_SIZE, BALL_SIZE)
        self.setAcceptDrops(True)
        self.setToolTip("把 .py 文件拖到这个球上\nAI 会在同目录生成「原名_修改版.py」")

        self._drag_offset = None
        self._status = "idle"        # idle | working | ok | fail
        self._status_text = "AI"
        self._queue = []
        self._worker = None

        self.move_to_corner()

    # ---------- 外观 ----------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)

        colors = {
            "idle": ("#4f6ef7", "#1e2f6b"),
            "working": ("#5b8def", "#27407a"),
            "ok": ("#4cd07d", "#1f7a44"),
            "fail": ("#ef6b6b", "#8a2f2f"),
        }
        c0, c1 = colors.get(self._status, colors["idle"])

        grad = QRadialGradient(rect.center(), rect.width() / 2)
        grad.setColorAt(0.0, QColor(c0))
        grad.setColorAt(1.0, QColor(c1))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawEllipse(rect)

        p.setPen(QColor("white"))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(16)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self._status_text)

    def set_status(self, status, text):
        self._status = status
        self._status_text = text
        self.update()

    def notify(self, text):
        QToolTip.showText(
            self.mapToGlobal(QPoint(self.width() // 2, self.height())), text, self
        )

    def move_to_corner(self):
        screen = QApplication.primaryScreen().availableGeometry()
        m = 16
        self.move(screen.left() + m, screen.bottom() - self.height() - m)

    # ---------- 拖动 ----------
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_offset = None

    # ---------- 右键菜单 ----------
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_reset = menu.addAction("回到左下角")
        menu.addSeparator()
        act_exit = menu.addAction("退出")
        chosen = menu.exec(event.globalPos())
        if chosen == act_reset:
            self.move_to_corner()
        elif chosen == act_exit:
            QApplication.quit()

    # ---------- 拖入 .py ----------
    @staticmethod
    def _py_urls(event):
        out = []
        for u in event.mimeData().urls():
            local = u.toLocalFile()
            if local.lower().endswith(".py") and os.path.isfile(local):
                out.append(local)
        return out

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._py_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        files = self._py_urls(event)
        if files:
            self.enqueue(files)

    # ---------- 任务队列 ----------
    def enqueue(self, files):
        self._queue.extend(files)
        if self._worker is None or not self._worker.isRunning():
            self._process_next()

    def _process_next(self):
        if not self._queue:
            self.set_status("idle", "AI")
            return
        batch = self._queue
        self._queue = []
        self._worker = Worker(batch)
        self._worker.file_started.connect(self._on_start)
        self._worker.file_done.connect(self._on_done)
        self._worker.file_failed.connect(self._on_fail)
        self._worker.all_finished.connect(self._on_all)
        self._worker.start()

    def _on_start(self, src):
        self.set_status("working", "…")
        self.notify(f"处理中：{os.path.basename(src)}")

    def _on_done(self, src, out):
        self.set_status("ok", "✓")
        self.notify(f"已生成：{os.path.basename(out)}")

    def _on_fail(self, src, err):
        self.set_status("fail", "✗")
        self.notify(f"失败：{os.path.basename(src)}\n{err}")

    def _on_all(self):
        QTimer.singleShot(1200, self._process_next)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ball = FloatingBall()
    ball.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
