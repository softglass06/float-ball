# -*- coding: utf-8 -*-
"""
AI 悬浮球

双击启动后，屏幕左下角出现一个悬浮球。把 .py 文件拖到球上，
程序会调用本机 `claude -p`（自动沿用 ccswitch 当前选中的供应商/额度），
让 AI 读懂文件里以注释 / docstring / 伪代码描述的需求，生成满足需求的
完整可运行代码，并以「原名_修改版.py」保存到原文件同一目录，不改动原文件。

依赖：PySide6；本机需已安装并配置好 Claude Code（含 ccswitch）。
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    import winreg

from _ed25519 import verify as ed25519_verify

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
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

BALL_SIZE = 84
CLAUDE_TIMEOUT = 600  # 秒，生成代码可能较慢
OUTPUT_SUFFIX = "_修改版"

# Ed25519 公钥（十六进制），用于校验 license.dat 签名。私钥在开发者 keygen.py 里，切勿外传。
PUBLIC_KEY_HEX = "4a6e644349f6802a38cc6cfc7e7005b734aab350e47e59a79733f1c286826cbf"

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


def _machine_guid() -> str:
    """返回一个尽量稳定的本机标识。

    Windows 用注册表 MachineGuid；macOS 用 IOPlatformUUID；其他回退到网卡 MAC。
    """
    if sys.platform == "win32":
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            if guid:
                return guid.strip()
        except OSError:
            pass
    elif sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, timeout=10,
            ).stdout.decode("utf-8", "replace")
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    val = line.split("=", 1)[-1].strip().strip('"')
                    if val:
                        return val
        except Exception:
            pass
    return "node-" + str(uuid.getnode())


def get_machine_code() -> str:
    """机器码 = 稳定本机标识的 SHA256 前 16 位，格式 XXXX-XXXX-XXXX-XXXX。"""
    guid = _machine_guid()
    digest = hashlib.sha256(guid.encode("utf-8")).hexdigest().upper()
    code = digest[:16]
    return "-".join(code[i:i + 4] for i in range(0, 16, 4))


def license_file_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "FloatBall" / "license.dat"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FloatBall" / "license.dat"
    return Path.home() / ".floatball" / "license.dat"


def verify_license_data(lic_machine_code: str, signature_b64: str) -> bool:
    """用内置公钥校验签名（私钥仅在开发者 keygen.py 中）。"""
    if not lic_machine_code or not signature_b64:
        return False
    try:
        pub = bytes.fromhex(PUBLIC_KEY_HEX)
        sig = base64.b64decode(signature_b64)
        return ed25519_verify(pub, lic_machine_code.encode("utf-8"), sig)
    except Exception:  # noqa: BLE001  # 签名不匹配或格式错误
        return False


def check_license_file(path) -> bool:
    """读取 license 文件并校验是否与本机机器码匹配。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    lic_mc = data.get("machine_code", "")
    if lic_mc != get_machine_code():
        return False
    return verify_license_data(lic_mc, data.get("signature", ""))


def is_licensed() -> bool:
    return check_license_file(license_file_path())


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


def _login_shell() -> str:
    """macOS/Linux 上从 GUI 双击启动的程序拿不到终端 PATH，需要用登录 shell
    运行 claude 才能补全 PATH（覆盖 homebrew / nvm 等不同安装方式）。"""
    for sh in ("/bin/zsh", "/bin/bash"):
        if os.path.exists(sh):
            return sh
    return "/bin/sh"


def call_claude(prompt: str) -> str:
    """调用本机 claude -p，返回最终文本（result 字段）。"""
    try:
        if sys.platform == "win32":
            # Windows 下 claude 是 .cmd 批处理脚本，必须 shell=True 才能被 cmd.exe 解析到。
            cmd = "claude -p --output-format json"
            proc = subprocess.run(
                cmd,
                input=prompt.encode("utf-8"),
                capture_output=True,
                timeout=CLAUDE_TIMEOUT,
                shell=True,
            )
        else:
            # -l 加载 ~/.zprofile，-i 再加载 ~/.zshrc；rc 里若有杂音，_parse_json 会兜底。
            proc = subprocess.run(
                [_login_shell(), "-lic", "claude -p --output-format json"],
                input=prompt.encode("utf-8"),
                capture_output=True,
                timeout=CLAUDE_TIMEOUT,
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


AGREEMENT_TEXT = (
    "该产品只能用于探讨和研究，\n"
    "严禁用于商业和考试，\n"
    "违反者后果自负。\n\n"
    "（最终解释权归作者所有）"
)


class AgreementDialog(QDialog):
    """启动时首先弹出的用户协议，必须勾选同意才能进入使用。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("用户协议")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setModal(True)

        label = QLabel(AGREEMENT_TEXT)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)

        self.check = QCheckBox("我已阅读并同意以上协议")
        self.btn_agree = QPushButton("同意并进入")
        self.btn_agree.setEnabled(False)
        self.btn_exit = QPushButton("退出")

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_agree)
        btn_row.addWidget(self.btn_exit)

        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(self.check)
        layout.addLayout(btn_row)

        self.check.toggled.connect(self.btn_agree.setEnabled)
        self.btn_agree.clicked.connect(self.accept)
        self.btn_exit.clicked.connect(self.reject)


class ActivationDialog(QDialog):
    """首次激活：展示本机机器码，导入 license.dat 校验。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("激活授权")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.setMinimumWidth(430)

        self.machine_code = get_machine_code()

        title = QLabel("请先激活本产品")
        f = title.font()
        f.setBold(True)
        f.setPointSize(12)
        title.setFont(f)

        hint = QLabel("下方是本机的机器码，请把它发给开发者，获取 license.dat 后导入。")
        hint.setWordWrap(True)

        self.code_edit = QLineEdit(self.machine_code)
        self.code_edit.setReadOnly(True)
        self.code_edit.setAlignment(Qt.AlignCenter)
        cf = self.code_edit.font()
        cf.setBold(True)
        cf.setPointSize(14)
        self.code_edit.setFont(cf)

        btn_copy = QPushButton("复制机器码")
        btn_import = QPushButton("导入 license.dat")
        btn_import.setDefault(True)

        row = QHBoxLayout()
        row.addWidget(btn_copy)
        row.addWidget(btn_import)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.code_edit)
        layout.addLayout(row)

        btn_copy.clicked.connect(self._copy)
        btn_import.clicked.connect(self._import)

    def _copy(self):
        QApplication.clipboard().setText(self.machine_code)
        QToolTip.showText(self.mapToGlobal(self.code_edit.pos()), "已复制", self)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 license.dat", str(Path.home()), "授权文件 (*.dat);;所有文件 (*)"
        )
        if not path:
            return
        if check_license_file(path):
            try:
                dst = license_file_path()
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(Path(path).read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                QMessageBox.warning(self, "激活失败", "授权文件已通过校验，但保存失败，请检查权限。")
                return
            QMessageBox.information(self, "激活成功", "授权成功，感谢使用！")
            self.accept()
        else:
            QMessageBox.warning(self, "激活失败", "授权文件无效，或与当前电脑的机器码不匹配。")


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 1) 用户协议（每次启动先弹出）
    dlg = AgreementDialog()
    if dlg.exec() != QDialog.Accepted:
        sys.exit(0)

    # 2) 激活校验（未激活时弹出）
    if not is_licensed():
        act = ActivationDialog()
        if act.exec() != QDialog.Accepted:
            sys.exit(0)

    ball = FloatingBall()
    ball.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
