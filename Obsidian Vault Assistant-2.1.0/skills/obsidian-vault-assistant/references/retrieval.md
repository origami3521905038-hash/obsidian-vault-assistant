# Retrieval SOP

Use a bounded, three-layer retrieval loop:

1. Identify the vault with `list_vaults`; if the user names one, still use the exact returned `vault_path`.
2. Call `search_tiered(scope="auto")`. It searches middle/evidence and Wiki first, then consults Raw only when there is no structured hit. Set `verify_with_raw=true` for provenance, conflicts, or a request for original wording.
3. Read only the best matching chapter with `read_note_section`. Use `read_note` for a complete document only when the question requires it.
4. If the evidence is weak, conflicting, or absent, say so. A score ranks retrieval candidates; it is not a truth or confidence score.

The layer heuristic is intentionally tolerant. Raw includes `raw/`, `99-原始素材/`, and `06-原始素材/`; middle includes `evidence/`, `evidence-cards/`, `facts/`, `cards/`, `00_Inbox/`, and source indexes; other content pages are Wiki. `templates/`, `ops/`, hidden directories, and `.obsidian` are excluded from normal search.

For a focused question, do not enumerate or read every note. The expected balance is a small middle/Wiki candidate set, one or two relevant sections, and a Raw lookup only when it improves verification.
