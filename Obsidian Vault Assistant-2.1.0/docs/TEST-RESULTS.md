# Acceptance Test Results

The release acceptance suite contains five isolated dialogue scenarios:

| Scenario | Result | Side effect |
| --- | --- | --- |
| First run with Obsidian absent | Passed | Installation and new-vault plans only |
| Markdown upload | Passed | Writes only to a temporary vault |
| Unsupported binary upload | Passed | Archives only in a temporary vault; no guessed text |
| Evidence-backed question | Passed | Reads temporary middle and Raw sections |
| Missing knowledge | Passed | Explicit no-direct-evidence response |

Run locally with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests/test_dialogues.py
```

The suite never calls `apply_vault_plan` for a real vault. Maintainers should repeat the real-vault hash check before shipping a new release.

Security regression coverage also verifies that external Markdown symlinks are not enumerated, heading section boundaries remain correct after the linear parser change, and DOCX entity declarations are rejected before XML parsing.
