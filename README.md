# codex-resume-win

原生 Windows 上的 Codex CLI 本地会话选择器。直接运行 `codex-resume`，搜索会话、查看预览，按回车继续聊天。

这是 [codex-resume](https://github.com/sunofch/codex-resume) 的 Windows 实现，沿用其通过 Codex app-server 列出跨账号本地会话、再调用原生 `codex resume` 的思路。终端界面使用 Windows 控制台 API 和 ANSI/VT 绘制，无需 WSL、curses 或第三方 Python 运行依赖。

## 功能

- 默认显示当前目录的会话，按 `Tab` 切换全部目录。
- 按标题、目录或会话 ID 搜索；多个词取交集，忽略大小写，支持中文输入。
- 方向键或鼠标选择，回车恢复会话，默认进入会话原目录。
- 宽屏显示列表和详情双栏，窄屏显示单栏，随终端尺寸重新布局。
- 兼容标准 npm 安装的 `codex.cmd`，通过 Node 直接运行其入口。
- 支持纯文本列表、恢复命令预览，以及在当前目录恢复。
- 退出时恢复备用屏幕、光标和控制台模式。

## 环境要求

- 原生 Windows、Python **3.10+**。
- Codex CLI 已安装、已配置，且 `codex` 在 PATH 中。
- 支持 ANSI/VT 的交互终端，推荐 Windows Terminal 中的 PowerShell 或 CMD。
- 终端至少 **42 列 × 16 行**；达到 100 列时显示双栏。

接口兼容性已在 Windows、Python 3.12、Codex CLI **0.172.0** 上验证。项目依赖实验性 app-server 接口，不保证兼容所有 Codex 版本。复杂 Emoji 的显示宽度可能与估算不同。

## 安装

安装了 [pipx](https://pipx.pypa.io/) 和 Git 后，在 PowerShell 中运行：

```powershell
pipx install git+https://github.com/sunofch/codex-resume-win.git
pipx ensurepath
```

重新打开终端后，直接使用：

```powershell
codex-resume
```

包名是 `codex-resume-win`，命令名是 `codex-resume`。如果已安装其他提供同名命令的工具，请先卸载或调整 PATH；可用 `Get-Command codex-resume -All` 检查实际入口。本项目尚未发布到 PyPI，安装时使用上述 Git 仓库地址。

不安装也可从源码运行：

```powershell
git clone https://github.com/sunofch/codex-resume-win.git
cd codex-resume-win
python codex_resume.py --all
```

Conda 用户可在已有环境中运行，例如 `conda run --no-capture-output -n tools python codex_resume.py --all`。项目不要求特定的 Conda 环境名。

## 使用

```powershell
codex-resume                  # 默认只显示当前目录
codex-resume --all            # 所有目录
codex-resume "重构"           # 初始搜索词
codex-resume --all "project"  # 跨目录搜索
codex-resume --all --here     # 在当前目录恢复
codex-resume --all --list     # 仅打印列表，不进入 TUI
codex-resume --all --dry-run  # 选择后只打印恢复命令
codex-resume --version
```

| 按键 | 操作 |
| --- | --- |
| `↑` / `↓` | 选择上一条 / 下一条 |
| `Enter` | 恢复所选会话 |
| 输入文字 | 搜索标题、目录或 ID |
| `Backspace` / `Ctrl+U` | 删除字符 / 清空搜索 |
| `Tab` | 切换当前目录与全部目录 |
| `PageUp` / `PageDown` | 翻页 |
| `Home` / `End` | 跳到首条 / 末条 |
| 鼠标单击 / 滚轮 | 选择 / 上下移动，取决于终端支持 |
| `Esc` / `Ctrl+C` | 退出 |

目录筛选不递归包含子目录，Windows 路径会规范大小写和分隔符。原目录不存在时提示并回退到当前目录。目录已移动时，可在新目录使用 `codex-resume --all --here`。

窄屏不显示右侧预览，只在列表下显示所选会话的工作目录。终端过小时会提示放大，此时仍可按 Esc 退出。重新运行工具可刷新会话数据。

### 参数

| 参数 | 说明 |
| --- | --- |
| `query` | 可选的初始搜索词 |
| `--all` | 显示所有目录；默认仅当前目录 |
| `--here` | 在启动工具的当前目录恢复 |
| `--list` | 打印 ID、更新时间、标题和目录，可用于重定向 |
| `--dry-run` | 在 TUI 中选择后，只打印 Windows 恢复命令 |
| `--codex PATH` | 指定 Codex 可执行文件或标准 npm 包装脚本 |
| `--version` | 显示版本 |

TUI 和 `--dry-run` 都要求输入输出连接交互终端。需要管道或重定向时使用 `--list`。

## 工作方式与边界

1. 临时启动 `codex app-server --stdio`，通过 JSON-RPC 初始化连接。
2. 分页调用 `thread/list`，传入 `currentAccountOnly: false` 和 `modelProviders: []`，按最近更新时间排序并去重。
3. 关闭临时服务，在终端内搜索、选择会话。
4. 用户按回车后，执行 `codex resume <ID> --cd <目录>`。

只显示接口返回的未归档交互会话，不同步其他设备，也不读取其他操作系统用户无法访问的数据。预览来自接口的 `preview` 字段，不是完整聊天记录。每次接口调用最多等待 30 秒，大量分页时总耗时可能更长。

**跨账号显示历史不会切换登录账号。** 恢复后仍使用当前 Codex 账号与配置；如果 Codex 拒绝恢复，本工具不会绕过检查。未进行跨账号切换恢复试验，实际可见范围与恢复权限以 Codex 为准。

工具沿用 `CODEX_HOME` 等当前环境，不直接修改历史数据库、登录凭据或 Codex 配置。官方 app-server 运行时可能生成正常日志与缓存。

## 故障排查

- **列表为空**：按 Tab 或使用 `--all`，检查目录是否移动，以及 `CODEX_HOME` 是否与原会话一致。
- **找不到 Codex**：检查 `Get-Command codex -All`。非标准包装脚本可使用 `--codex` 指定实际 `codex.exe`。
- **接口错误或读取超时**：检查 `codex --version` 和 `codex app-server --stdio`，提交脱敏后的错误与版本信息。
- **中文显示异常**：使用 UTF-8 终端和支持中文的字体。Python 输出编码需要时可设置 `$env:PYTHONUTF8='1'`。
- **拒绝访问**：检查运行环境是否限制 Codex 数据目录或进程启动。工具不要求管理员权限。

## 更新与卸载

```powershell
pipx upgrade codex-resume-win
pipx uninstall codex-resume-win
```

卸载不会删除 Codex 会话。源码运行的用户可使用 `git pull --ff-only` 更新。

## 开发与验证

```powershell
python -m unittest discover -s tests -v
python tests/console_smoke.py
```

普通自动测试使用虚构会话和模拟服务，不需要登录。`console_smoke.py` 仅适用于 Windows，会创建隐藏控制台验证绘制、中文输入、导航、选择、退出与模式恢复。可用 `--launcher <启动脚本路径>` 额外验证真实命令入口；该模式需要已配置的 Codex，只执行 `--dry-run`，不进入会话。

2026-11-25：16 项本地自动测试通过，包含非 UTF-8 子进程环境和路径规范化的回归验证；真实 Windows 控制台测试及真实会话的命令入口 `--all --dry-run` 验证通过。未实际进入恢复后的会话。多版本 Windows 自动测试的最新结果见仓库的 Actions 页面。

提交问题时请勿附带账号令牌、完整聊天记录或其他敏感信息。

## 许可证

[MIT](LICENSE) © 2026 sunofch。会话读取与恢复的实现参考同一作者的 [codex-resume](https://github.com/sunofch/codex-resume)，保留其 MIT 版权声明。
