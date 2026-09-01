# Obsidian Vault Assistant

Obsidian Vault Assistant is a local-first Codex skill and MCP server for people who want a searchable, maintainable Markdown knowledge base. It discovers configured Obsidian vaults, searches a three-layer model (Raw, evidence/middle, and Wiki), reads only the relevant chapter, and stages every change for explicit review.

## What it does

- Checks the operating system, CPU architecture, Python runtime, Obsidian installation, and candidate vault roots.
- Provides an install plan for macOS, Windows x86_64, and Linux when a supported package manager is available. The plan is explicit and no installer runs without confirmation.
- Finds multiple vaults under iCloud Drive, local folders, or `OBSIDIAN_VAULT_ROOT`/`OBSIDIAN_VAULT_PATH`.
- Balances speed and accuracy: middle/Wiki first, Raw on demand or when structured matches are absent.
- Archives uploaded files and decomposes them into an original attachment, a Raw note, and an evidence card. Unsupported binary files are archived without guessed content.
- Uses SHA-256 fingerprints, exclusive creation, path checks, and a plan-plus-confirm transaction for writes.

Cloud sync is intentionally not automated. Configure iCloud Drive, OneDrive, Syncthing, or another provider yourself.

## Install in Codex

1. Download or clone this repository into the local plugin directory supported by your Codex installation.
2. Keep the repository root as the plugin root so `.codex-plugin/plugin.json`, `.mcp.json`, `skills/`, and `scripts/` stay together.
3. Reload Codex and invoke **Obsidian Vault Assistant**.
4. On first use, run the environment check and follow the displayed vault-selection and bootstrap plan.

The MCP configuration uses `python3`, a relative script path, and `cwd: "."`; it contains no machine-specific absolute paths. On Windows, use a Python installation whose `python3` command is available, or adjust the command in your local installation to `python`.

## Typical retrieval

```text
check_environment()
list_vaults()
get_vault_profile(vault_path=...)
search_tiered(query="...", scope="auto", verify_with_raw=false)
read_note_section(vault_path=..., file_path=..., heading=...)
```

Answers grounded in a vault must contain an investigation conclusion, Raw provenance, and confidence analysis. When no relevant note exists, say so explicitly instead of filling the gap with unstated general knowledge.

## Typical upload

```text
inspect_uploaded_file(upload_path="...", vault_path=...)
plan_file_ingest(upload_path="...", vault_path=..., title="...", claim="...")
apply_vault_plan(plan_id="...", confirm=true)  # only after exact-plan approval
```

See [docs/TUTORIAL.md](docs/TUTORIAL.md) and [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md) for the complete SOP. See [SECURITY.md](SECURITY.md) for local privacy boundaries.

## Development checks

```bash
python3 -m py_compile scripts/environment.py scripts/vault_server.py
python3 -m unittest -v tests/test_dialogues.py
```

The tests create temporary vaults. They do not write to a user's real Obsidian vault.

## License

MIT. See [LICENSE](LICENSE).
