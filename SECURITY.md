# Security

AI Project Finder is designed to run locally and bind only to `127.0.0.1`.

The generated index may contain prompt excerpts, workspace paths, and filenames. Do not commit or share `data/index.json`, `data/open.log`, or an installed folder containing generated data. These files are excluded by the repository's `.gitignore`.

To report a security issue, open a private security advisory in the GitHub repository instead of a public issue.
