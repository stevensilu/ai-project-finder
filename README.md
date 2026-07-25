# AI Project Finder

AI Project Finder is a local-only search dashboard for finding which AI tool handled a project, file, client, or prompt. It indexes local session metadata from Codex, Claude, and Kimi, then opens the matching session or workspace from one page.

No prompt content is uploaded. The server listens on `127.0.0.1` and stores its index inside this folder.

## Supported sources

| Source | Automatic index | Open behavior |
|---|---|---|
| Codex | `~/.codex/sessions` or `$CODEX_HOME/sessions` | Opens the Codex task |
| Claude Code | `~/.claude/projects` or `$CLAUDE_CONFIG_DIR/projects` | Opens the matching Claude Desktop session when its local mapping exists; otherwise uses Claude's resume link |
| Kimi Code CLI | `~/.kimi-code/sessions` or `$KIMI_CODE_HOME/sessions` | Opens the session in Kimi Code Web or resumes it in Terminal |
| Kimi Desktop Work | Auto-detects the local Work runtime on macOS | Opens Kimi Desktop Work |
| Kimi cloud/web chats | Add the conversation URL with **Add trace** | Opens the saved web conversation |

Kimi Desktop's regular cloud-chat database is not a stable public file format. The app automatically indexes Kimi Desktop Work sessions that expose local Kimi Code state. Regular web or cloud conversations can still be saved as manual traces.

## Install on macOS

1. Download and unzip the release.
2. Move the folder to a stable location, such as `~/Applications/AI Project Finder`.
3. Control-click `install.command`, choose **Open**, and approve the first launch.
4. The dashboard opens at `http://127.0.0.1:4388`.

The installer also creates:

```text
~/.local/bin/ai-project-finder
```

You can later start the app by double-clicking `start.command` or running that command in Terminal.

Requirements: macOS and Python 3.10 or newer. The app uses only Python's standard library.

## Automatic path detection

The committed `config.json` uses `auto` for every source. AI Project Finder resolves the current user's home directory at runtime and honors the tools' standard environment variables:

```text
CODEX_HOME
CLAUDE_CONFIG_DIR
KIMI_CODE_HOME
```

For a custom location, replace `auto` with a path or list of paths:

```json
{
  "sources": {
    "claude": ["~/.claude/projects", "/Volumes/Archive/claude-projects"]
  }
}
```

Paths support `~` and environment variables. No username or `/Users/...` path is built into the public configuration.

## Use

- Search by project, client, prompt keyword, workspace, filename, or artifact.
- Switch between **Sessions** and **Projects**.
- Use **Open session** to return to the original AI client.
- Kimi Code results also provide **Open CLI**.
- Use **Add trace** for AI tools or cloud chats that do not expose a local history format.
- Use **Refresh** after installing a new AI tool or creating new sessions.

Exact session deep links depend on what each AI client exposes. Codex and mapped Claude Desktop sessions can open directly. Kimi Desktop Work currently opens the Work surface because its macOS URL scheme does not expose a stable conversation-level deep link.

## Run for development

```bash
python3 app.py --open
python3 -m unittest discover -s tests
```

The app binds only to localhost. Generated files such as `data/index.json` and local open diagnostics are excluded from Git.

## Reference

Kimi Code's official command reference documents `kimi --session`, `kimi web`, and `KIMI_CODE_HOME`:

https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command.html

## License

MIT
