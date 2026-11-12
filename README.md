# AI 小白悬浮球

双击启动后，屏幕**左下角**出现一个悬浮球。把 `.py` 文件拖到球上，AI 会：

1. 读懂文件里以注释 / docstring / 伪代码描述的**需求**；
2. 生成满足需求的**完整可运行代码**；
3. 以 `原名_修改版.py` 保存到原文件**同一目录**，**不改动原文件**。

## 工作原理

- AI 能力通过调用本机 `claude -p` 实现，自动沿用 **ccswitch 当前选中的供应商和额度**，你零成本、零服务器。
- 调用输出用 `--output-format json` 解析（答案在 stdout，告警在 stderr，只取 stdout）。
- 读取 .py 时兼容 UTF-8（带/不带 BOM）和 GBK 编码；生成文件统一写 UTF-8、LF 换行。

## 使用

- **用户协议**：每次启动先弹出，勾选「同意」才能进入。
- **激活**：首次使用需激活——激活窗口会显示本机**机器码**（可一键复制），把它发给开发者换取 `license.dat`，导入后即可进入；后续启动自动跳过。
- **拖入文件**：把 `.py` 拖到球上即可（支持一次拖多个，逐个处理）。
- **拖动球**：按住左键拖动悬浮球到任意位置。
- **回到左下角 / 退出**：在球上点右键。
- 处理中球显示 `…`，成功 `✓`，失败 `✗`，并用气泡提示结果。

## 授权（开发者）

- 授权采用 **Ed25519 非对称签名**：机器码 + 签名的 `license.dat`。私钥只在 `keygen.py` 里，exe 里只内置公钥，因此即使有人从 exe 里扒出公钥也无法伪造授权文件。
- 开发者工具 `keygen.py`（在桌面，**含私钥，切勿外传或提交到仓库**）：双击运行 → 输入用户机器码 → 生成 `license.dat` → 发给用户。
- 一台电脑对应一个机器码；换机器 / 重装系统会导致机器码变化，需重新授权。

## 构建 exe

前置：本机已装 Python 3.9+，且已安装并配置好 Claude Code / ccswitch。

```bat
cd /d D:\codework\floatball
pip install -r requirements.txt
build.bat
```

生成物在 `dist\FloatBall.exe`，双击即可运行。可以自行把它重命名成任意名字（如 `悬浮球.exe`），PyInstaller 单文件 exe 改名不影响运行。

## 构建 Mac 版（.app）

PyInstaller 不能跨平台交叉编译，Mac 版必须在 macOS 上打包。仓库内置 GitHub Actions 工作流 `.github/workflows/build-macos.yml`，用免费 macOS 环境自动打包，无需本机有 Mac：

1. 把代码推到 GitHub，到 **Actions** 页 → 左侧 `build-macos` → **Run workflow** 手动触发；
2. 跑完后在产物里下载 `FloatBall-mac.zip` 或 `FloatBall.dmg`，微信发给 Mac 用户即可（`.app` 可直接重命名成「悬浮球.app」）。

首次在对方 Mac 上双击会被 Gatekeeper 拦截（未签名），需右键（或按住 Control 点）→「打开」→ 再点「打开」一次即可，之后正常双击。机器码在 Mac 上取 IOPlatformUUID，首次会弹激活窗，同样把机器码发给你用 keygen 生成 `license.dat`。

## 已知限制 / 说明

- 生成的文件名固定为 `原名_修改版.py`，同名会**覆盖**旧结果。
- 大文件超出模型上下文时会失败并提示，建议单文件内容控制在合理范围。
- 若模型偶发输出多余说明文字导致代码不纯，可在 `SYSTEM_PROMPT` 里加强约束，或再跑一次。
