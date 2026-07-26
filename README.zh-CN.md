# AI Project Finder

[English](README.md) · [简体中文](README.zh-CN.md)

**面向 Codex、Claude 与 Kimi 本地记录的跨 AI 项目搜索索引。**

可通过项目、客户、提示词片段、工作目录或文件名定位记录，并返回相应的 AI 会话与项目路径。

![平台](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-111111)
![Python](https://img.shields.io/badge/Python-3.10%2B-111111)
![数据](https://img.shields.io/badge/data-local_only-111111)
![许可](https://img.shields.io/badge/license-MIT-111111)

> 当前状态：v1.3.0，提供独立的英文版与中文版，均支持 macOS 与 Windows。

## AI Project Finder 的定位

同一项工作可能分布在多个 AI 工具中。研究从 Claude 开始，代码在 Codex 中继续，最后由 Kimi 补充处理。过一段时间后，仍然容易记得的线索通常是客户名称、`launch-plan.xlsx` 这样的文件名，或原始需求中的一句话。

AI Project Finder 将这些本地记录整理到一个搜索入口，便于定位：

- 处理过该工作的 AI 工具。
- 保留相关上下文的会话。
- 与该会话关联的项目目录或产出文件。

索引保留在创建这些会话的电脑上。

## 演示

这段约 26 秒的中文版演示在完全隔离的演示模式中运行，使用虚构项目与合成 AI 历史，不会读取本地会话、索引、路径或手工记录。

https://github.com/user-attachments/assets/193f0c7a-6d1f-4fbf-a9d9-be2834b9ce15

演示内容包括：

1. 通过记得的“阿特拉斯发布”关键词搜索。
2. 切换浅色与深色主题。
3. 从“会话”切换到“项目”。
4. 筛选 Claude，并预览打开会话的动作。

## 核心功能

### 跨 AI 本地索引

AI Project Finder 可以读取 Codex、Claude Code、Kimi Code，以及兼容的 Kimi Desktop Work 本地历史。浏览器工具和云端聊天可以通过手工记录补充。

默认自动识别：

```text
Codex        ~/.codex/sessions 或 $CODEX_HOME/sessions
Claude Code  ~/.claude/projects 或 $CLAUDE_CONFIG_DIR/projects
Kimi Code    ~/.kimi-code/sessions 或 $KIMI_CODE_HOME/sessions
```

### 从仍记得的线索开始搜索

搜索范围包括：

- 会话标题
- 项目与客户名称
- 提示词摘要
- 工作目录
- 被引用的文件名与产出文件

搜索不区分大小写。输入多个关键词时，匹配结果需要包含全部关键词。

搜索框支持两种写法：

```text
"launch checklist"     作为完整短语匹配，而不是拆成多个词
source:claude          只看某一个工具的会话
```

结果排序会参考关键词命中的位置，并让较新的会话稍微靠前。搜索词、来源筛选、视图、时间范围和排序方式都会同步到地址栏，刷新后仍在，也可以存成书签或在本机以链接形式打开。

### 会话与项目视图

**会话**展示单次对话。**项目**将同一项目线索下的多次 AI 处理记录聚合展示。

结果还可以按 AI 来源、更新时间、相关度和新旧顺序筛选。

### 返回原始工作位置

根据来源和系统环境，结果可以提供：

- 打开原始 AI 会话
- 打开工作目录
- 在 Terminal 中恢复 Kimi Code 会话
- 打开 Kimi Code Web
- 打开已保存的网页会话或本地路径
- 复制工作目录或会话位置

具体动作取决于 AI 客户端、操作系统、已注册的 URL scheme 和本地会话元数据。

## 环境要求

- macOS 或 Windows
- Python 3.10 或更高版本
- 至少一种受支持的本地 AI 历史，或手工添加的记录

Python 应用只使用标准库，无需执行 `pip install`。

## 安装

### macOS

#### 下载发行版

1. 下载并解压 [AI Project Finder v1.3.0 中文版 macOS 安装包](https://github.com/stevensilu/ai-project-finder/releases/download/v1.3.0/AI_Project_Finder_ZH_macOS_v1.3.0.zip)。
2. 将文件夹移动到稳定位置，例如 `~/Applications/AI Project Finder`。
3. 按住 Control 点击 `install.command`，选择 **打开**，完成首次运行确认。
4. 应用会在 `http://127.0.0.1:4388` 打开。

安装器会创建：

```text
~/.local/bin/ai-project-finder
```

后续可以双击 `start.command`，或运行：

```bash
~/.local/bin/ai-project-finder
```

#### 通过 Git clone 安装

仓库中的默认语言是英文。完成 clone 后，在安装前将 `config.json` 中的 `"locale"` 改为 `"zh-CN"`。

```bash
git clone https://github.com/stevensilu/ai-project-finder.git
cd ai-project-finder
chmod +x install.command start.command
./install.command
```

### Windows

#### 下载发行版

1. 下载并解压 [AI Project Finder v1.3.0 中文版 Windows 安装包](https://github.com/stevensilu/ai-project-finder/releases/download/v1.3.0/AI_Project_Finder_ZH_Windows_v1.3.0.zip)。
2. 将文件夹移动到稳定位置，例如 `%LOCALAPPDATA%\Programs\AI Project Finder`。
3. 双击 `install.bat`。
4. 应用会在 `http://127.0.0.1:4388` 打开。

安装器会创建：

```text
%LOCALAPPDATA%\AIProjectFinder\ai-project-finder.bat
```

后续可以双击 `start.bat`，或使用上述本地启动器。

#### 通过 Git clone 安装

仓库中的默认语言是英文。完成 clone 后，在安装前将 `config.json` 中的 `"locale"` 改为 `"zh-CN"`。

```powershell
git clone https://github.com/stevensilu/ai-project-finder.git
cd ai-project-finder
.\install.bat
```

Windows 可能对下载的批处理文件显示 SmartScreen 提示。可以先用文本编辑器查看脚本，再选择 **仍要运行**。

## 使用方法

### 搜索历史项目

搜索框可以输入仍记得的任意线索，例如：

```text
Orchid launch
wholesale forecast
campaign-brief.pdf
landing page localization
```

输入过程中结果会实时更新。按回车键或点击**搜索**后，页面会移动到第一条结果。

中文版安装包中的界面、安装提示、空状态和演示模式均使用中文。被索引的会话标题和摘要会保留原始语言。

### 缩小结果范围

- 选择一个或多个 AI 来源。
- 切换**会话**或**项目**。
- 将更新时间限定为最近 30 天、90 天或一年。
- 按相关度、最新或最早排序。

### 打开结果

结果可能提供**打开会话**、**打开工作区**、**打开命令行**、**复制路径**或**复制会话信息**。可用动作会根据来源和客户端集成情况变化。

### 添加浏览器端记录

选择**添加记录**，保存：

- AI 工具
- 项目或客户
- 记录标题
- 会话链接或本地路径
- 搜索关键词

已保存的记录会在结果卡片上带有**编辑**和**删除**两个动作，链接或标题写错了可以直接改，不必手动打开文件。删除需要再点一次确认。

手工记录会写入 `data/manual.json`。第一次保存时会自动创建这个私有运行文件，并且该文件已排除在 Git 之外。仓库中的 `data/manual.example.json` 是空白示例。

### 刷新索引

创建新会话、安装新的 AI 工具、移动会话目录或修改来源路径后，可以使用**刷新**更新索引。

刷新只会重新读取自上次以来大小或时间戳发生变化的转写文件，通常在一秒以内完成。已解析的记录保存在 `data/parse-cache.json`，同样是排除在 Git 之外的私有运行文件；删除它会触发一次完整重读。

启动时的那次刷新在后台进行。已经打开的 dashboard 会在它完成后自动更新列表。

### 自定义来源路径

默认 `config.json` 使用自动识别：

```json
{
  "port": 4388,
  "max_prompt_chars": 9000,
  "locale": "zh-CN",
  "sources": {
    "codex": "auto",
    "claude": "auto",
    "kimi": "auto",
    "kimi-desktop": "auto"
  }
}
```

来源可以替换为单一路径或多个路径：

```json
{
  "sources": {
    "claude": [
      "~/.claude/projects",
      "/Volumes/Archive/claude-projects"
    ]
  }
}
```

Windows JSON 路径需要使用转义后的反斜线：

```json
{
  "sources": {
    "claude": "C:\\Users\\name\\.claude\\projects"
  }
}
```

macOS 上使用多个 Claude Desktop profile 时，可以设置：

```bash
export AI_PROJECT_FINDER_CLAUDE_PROFILES="Claude,Claude-Work"
```

### 项目命名

记录的项目名来自它的工作目录路径。每个人的目录习惯不同，所以识别标记可以配置：

```json
{
  "naming": {
    "project_markers": ["projects"],
    "client_markers": ["clients", "客户"],
    "dated_workspace_markers": ["codex"],
    "ignore_dirs": ["documents", "downloads", "desktop", "tmp", "new-chat"]
  }
}
```

- `project_markers`：该目录的下一层就是项目名，例如 `.../projects/atlas-launch`。
- `client_markers`：该目录的下一层是客户名。允许编号前缀，所以 `客户` 也能匹配名为 `1.1 客户` 的文件夹。
- `dated_workspace_markers`：按日期分层存放的工作区，例如 `.../codex/2026-07-25/atlas-launch`。
- `ignore_dirs`：过于泛泛、不适合当项目名的文件夹。主目录始终按此处理。

形如 `clone-https-github-com-someone-project` 这种由需求转成的 slug 目录，不会被当作项目名。路径里取不到名字时，记录保持未归类，不再从首条需求里推测名字：那种做法会给每个会话造出一个一次性项目，把真正的项目淹没掉。

### 把工作归入项目

每条结果都带**归入**动作。填写项目名，并选择只作用于这一次会话，还是作用于它所在的整个工作目录。按目录归入时，之后在该目录新建的会话会自动继承。项目分组上有**重命名**，会把该组所有记录改到新名称。清空名称则取消归属，恢复自动推导的标签。

归属关系写入 `data/projects.json`，属于排除在 Git 之外的私有运行文件。它在每次重建时重新应用，不会被固化进解析缓存，因此刷新后依然有效。

## 隐私与安全

AI Project Finder 围绕本地会话数据运行：

- Python server 只绑定 `127.0.0.1`。
- 原始会话文件保持只读。
- 应用没有上传或 analytics endpoint。
- 生成的索引与打开诊断文件已排除在 Git 之外。
- 打开动作会调用已安装的 AI 客户端、Terminal、Explorer 或 Finder，或已保存的 URL。

### 本地 API 边界

绑定 `127.0.0.1` 可以挡住其他电脑，但单靠这一点还不足以阻止同一浏览器里打开的网站访问本地服务。API 另外做了四层校验：

- 每次启动生成一个会话 token，dashboard 页面通过 `SameSite=Strict`、`HttpOnly` cookie 拿到它，`/api/` 请求缺少该 token 会被拒绝。
- `Host` 头不是本地回环地址的请求会被拒绝，这一条用于阻断 DNS rebinding。
- 跨站 POST 请求会被拒绝，请求体必须是 `application/json`，跨源 preflight 不会获得授权。
- 响应带有内容安全策略，页面无法加载或访问任何外部来源。

重新启动应用会签发新的 token，已打开的 dashboard 标签页会自动刷新一次来获取。

生成的索引可能包含提示词摘要、文件名和工作目录。分享已安装的应用副本前，建议将 `data/` 目录视为私有数据并进行检查。

手工记录可能包含私人链接或项目名称。`data/manual.json` 已明确排除在版本控制之外。

界面所需的字体和动画文件已经打包在应用中。打开 dashboard 时不需要访问字体或动画 CDN。

安全问题反馈方式见 [SECURITY.md](SECURITY.md)。

## 当前限制

- 当前搜索基于关键词，不使用 semantic embeddings 或 fuzzy matching。
- 精确会话链接取决于各 AI 客户端公开的 URL scheme 与本地元数据。
- Claude Desktop 映射适用于存在对应 Desktop metadata 的 Claude Code 会话，不索引普通 Claude 云端聊天。
- Kimi Desktop Work 会在 macOS 和常见 Windows AppData 布局中识别内嵌的 Kimi Code runtime。目前会打开 Work 页面，暂时没有稳定的精确会话 deep link。
- 普通 Kimi 云端或网页会话需要通过手工记录补充。
- 移动或删除源会话文件后，旧结果可能需要刷新索引才能恢复正确状态。
- 英文版与中文版会统一应用生成的界面文案。已有 AI 会话中的内容会保留原始语言。
- Windows 端可以使用通用 home-directory discovery 建立索引。单条会话的打开能力取决于对应 Windows 客户端与已注册的 protocol。

## 故障排查

### 需要 Python 3.10 或更高版本

可从以下地址安装：

- [Python for macOS](https://www.python.org/downloads/macos/)
- [Python for Windows](https://www.python.org/downloads/windows/)

也可以通过 `AI_PROJECT_FINDER_PYTHON` 指定 Python。

macOS：

```bash
export AI_PROJECT_FINDER_PYTHON="/path/to/python3"
```

Windows Command Prompt：

```bat
set AI_PROJECT_FINDER_PYTHON=C:\Path\To\python.exe
```

### macOS 无法打开 `install.command`

按住 Control 点击文件，选择 **打开**，完成首次运行确认。安装器启动后会移除项目文件夹的 quarantine attribute。

### Windows 无法打开 `install.bat`

可以先用文本编辑器检查批处理内容。如果 SmartScreen 出现，选择 **更多信息**，再选择 **仍要运行**。

### 某个来源显示零会话

1. 确认对应 AI 工具已经创建过本地会话。
2. 检查默认路径或环境变量。
3. 使用**刷新**。
4. 工具使用非标准目录时，在 `config.json` 中补充自定义路径。

### 会话无法打开

- 确认原始 AI 客户端仍然已安装。
- 确认被索引的 transcript 仍然存在。
- 移动会话文件后刷新索引。
- 可以在 `data/open.log` 查看本地打开模式与错误类型。

### Kimi Web 无法打开

`kimi` binary 需要位于：

```text
$KIMI_CODE_HOME/bin/kimi
~/.local/bin/kimi
PATH
```

Kimi Code 命令行为以[官方 command reference](https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command.html)为准。

### 页面提示本地会话已失效

应用重启后签发了新的会话 token。页面会自动刷新一次来获取新 token。如果提示仍在，可以手动刷新浏览器标签页，并确认地址是 dashboard 的 URL，不是另存出来的 `index.html` 副本。

### 4388 端口已被占用

已有实例可能正在运行，可以打开：

```text
http://127.0.0.1:4388
```

也可以在 `config.json` 中设置其他端口。

## 后续规划

正在考虑的方向：

- 带签名的 macOS 与 Windows 应用安装包
- 更多本地 AI 历史适配器
- 在客户端提供稳定接口后改善会话级 deep link
- 可选的 semantic 与 fuzzy search
- 手工记录的导入与导出
- 更多界面语言
- 更完整的 Windows 与 Linux 集成

后续规划中的内容仍在评估，暂未承诺发布时间。

## 参与贡献

欢迎提交 issue 与 pull request。

提交问题时，建议避免附加生成的索引、打开日志、手工记录、包含客户名称的截图或真实会话记录。解析器改进与缺陷报告可以优先使用合成测试数据。

开发命令：

```bash
python3 app.py --open
python3 app.py --build-only
python3 app.py --demo
python3 -m unittest discover -s tests
```

## 许可证

采用 MIT 许可证，详见 [LICENSE](LICENSE)。
