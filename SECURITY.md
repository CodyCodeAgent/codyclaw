# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in CodyClaw, please report it responsibly:

1. **Do NOT** open a public GitHub issue.
2. Email your report to the maintainers (see `pyproject.toml` for contact info), or use [GitHub Security Advisories](https://github.com/CodyCodeAgent/codyclaw/security/advisories/new).
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will acknowledge receipt within **48 hours** and aim to provide a fix within **7 days** for critical issues.

## Security Considerations

CodyClaw connects AI agents to Feishu and executes code on the host machine. Operators should be aware of the following:

- **API keys**: Store `ANTHROPIC_API_KEY` and Feishu credentials in environment variables, not in config files committed to version control.
- **Network exposure**: The management API (`/health`, `/api/*`, Web Console) has no built-in authentication. Bind to `127.0.0.1` or use a reverse proxy with auth in production.
- **Agent permissions**: Use `cody.permissions` and `cody.security` in `config.yaml` to restrict what agents can do (command timeouts, blocked commands, tool-level permissions).
- **Human-in-the-loop**: Enable `confirm` permission level for sensitive operations to require explicit user approval.
