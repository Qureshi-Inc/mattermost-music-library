# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | Yes                |

## Security Principles

### No Shell Injection

All subprocess calls use explicit argument lists. `shell=True` is never used anywhere in the
codebase. The `yt-dlp` integration passes arguments as arrays, never as interpolated strings.

### URL Validation

All user-supplied URLs are validated before processing:
- Only `https://` schemes are accepted for download sources
- URLs are parsed and validated against an allowlist of supported domains
- No open redirects or SSRF vectors through URL parameters

### Secret Redaction

Sensitive values (API keys, tokens, webhook URLs) are:
- Never logged at any log level
- Redacted from error messages and stack traces
- Excluded from API responses via Pydantic model serialization
- Stored only in environment variables, never in code or config files

### Non-Root Containers

The Docker container runs as a dedicated `slaptastic` user (non-root):
- The application process has no elevated privileges
- File system permissions are restricted to application directories only
- No capability escalation is possible within the container

### Input Sanitization

All user inputs are sanitized before use:
- File paths are normalized and validated against path traversal attacks
- Query parameters are type-checked via Pydantic models
- File names are sanitized to remove special characters before writing to disk
- Maximum request body sizes are enforced

### Dependency Scanning

Dependencies are monitored and updated regularly:
- Dependabot is configured for weekly dependency updates
- Only pinned or minimum-version dependencies are used
- No dependencies with known critical vulnerabilities are permitted
- The dependency tree is kept minimal to reduce attack surface

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. Do NOT open a public GitHub issue
2. Email security concerns to the maintainers directly
3. Include steps to reproduce the vulnerability
4. Allow reasonable time for a fix before public disclosure

We aim to acknowledge reports within 48 hours and provide a fix within 7 days for critical issues.
