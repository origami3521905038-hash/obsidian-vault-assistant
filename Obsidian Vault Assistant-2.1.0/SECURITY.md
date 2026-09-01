# Security and Privacy

## Local-first boundary

The MCP server reads local Markdown and explicitly supplied upload files. It has no HTTP client and does not transmit vault content. Cloud sync is outside the plugin and remains user-configured.

## Write boundary

All vault mutations are represented by a plan. `apply_vault_plan` requires `confirm: true`, validates the selected vault, checks SHA-256 fingerprints for changed targets and uploads, creates files exclusively, and rolls back partial operations where possible. Hidden directories, `.obsidian`, path traversal, and overwrites are rejected.

Obsidian installation is separate from vault writing. Package-manager commands are fixed argument arrays, run with `shell=False`, and require a fresh `plan_id` plus explicit confirmation.

## Reporting issues

Please do not include private note content in public issues. Report a minimal reproduction, operating system, plugin version, tool name, and sanitized error. For sensitive issues, use a private security channel in the hosting repository.

## Development invariant

The release tests use temporary vaults. Maintainers must snapshot or hash real vault Markdown files before final verification and confirm that no real note changed.

## Parser and discovery hardening

Markdown discovery resolves every candidate and rejects links that leave the selected vault. Heading boundaries are computed in a linear pass so a single note cannot trigger suffix-scan CPU amplification. DOCX extraction rejects DTD/entity declarations before parsing and uses `defusedxml` when available; documents that fail extraction remain archive-only and are never treated as verified text.
