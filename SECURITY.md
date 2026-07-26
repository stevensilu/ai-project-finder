# Security

AI Project Finder is designed to run locally and bind only to `127.0.0.1`.

## Data handling

The generated index may contain prompt excerpts, workspace paths, and filenames. Do not commit or share `data/index.json`, `data/open.log`, or an installed folder containing generated data. These files are excluded by the repository's `.gitignore`.

## Local threat model

The service is reachable from the computer it runs on, which includes any website open in a browser on that computer. Loopback binding alone does not separate those two callers, so the API applies these checks:

| Check | What it prevents |
|---|---|
| A session token, issued to the dashboard page as a `SameSite=Strict` `HttpOnly` cookie and required on every `/api/` request | A page on another site reaching the API after guessing the port |
| The `Host` header must be the local loopback address | DNS rebinding, where a site points its own hostname at `127.0.0.1` and reads the API as same-origin |
| POST requests must be same-origin with an `application/json` body, and preflight requests are refused | A cross-site form or simple request triggering a reindex, a manual write, or an open action |
| A content security policy limiting the page to its own origin | The dashboard loading from or contacting an external origin |

Each launch generates a new token, so restarting the application invalidates the previous one.

Outside this model: another program already running under the same user account can read the source transcripts directly, and these checks do not attempt to prevent that.

## Reporting

To report a security issue, open a private security advisory in the GitHub repository instead of a public issue.
