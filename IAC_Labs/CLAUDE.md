# Network as Code Lab Series

## Reference Documents
- LABS.md: Master blueprint for the 7-lab series. Follow its structure, tooling choices, and repo layout.

## Enforced Standards
- All code must comply with the secure-build-standard in ~/.claude/CLAUDE.md. No exceptions without documented justification.
- No secrets in code (SEC-12). Credentials in env vars or .env (gitignored).
- Pin all dependency versions exactly (SEC-30).
- Package manager: uv only. No pip, no pip install, no requirements.txt. Use `uv init`, `uv add`, `uv sync`, `uv run`. Commit uv.lock.
- Type hints and docstrings on all public interfaces.
- async for all device connections (Scrapli).
- Fail loud with clear error messages.

## Writing Style
- No em dashes
- Paragraph form over bullets in docs
- Peer-to-peer conversational tone, no corporate language
- No "troubleshooting at 2 AM" trope

## Project Context
- Lab environment: ContainerLab with FRR routers
- Primary tools: Python, Scrapli, Pydantic, Jinja2, pytest
- Source of truth: Git-native (primary), NetBox/Nautobot (optional paths)
- CI/CD: GitHub Actions
- This code will be published publicly on GitHub under E-Conners-Lab
