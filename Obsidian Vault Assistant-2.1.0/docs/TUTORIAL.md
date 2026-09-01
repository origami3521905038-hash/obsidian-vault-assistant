# Practical Tutorial

## 1. First run

Call `check_environment`. It is read-only. If Obsidian is missing, call `plan_environment_setup`, show the `plan_id` and exact package-manager command, and ask for approval. Apply only the approved plan with `apply_environment_setup(plan_id, confirm=true)`. If the result is `manual_required`, install from <https://obsidian.md/download> yourself and run the check again.

Then call `list_vaults`. Select one exact `vault_path`; a vault name is not enough when names collide. Use `get_vault_profile` and `audit_vault_structure` to learn the existing layout. If no vault exists, call `plan_new_vault` with a local root, review the folders and templates, and apply only after explicit approval.

## 2. Query workflow

For a normal question, use `search_tiered(scope="auto")`. The service searches evidence/middle and Wiki first. It calls Raw only when there is no structured hit. Add `verify_with_raw=true` when the user asks for source wording, a date, an original interview, or a conflict check. Read the returned heading with `read_note_section`; this keeps context small and preserves the chapter boundary.

Return:

1. Investigation conclusion, labeled as a vault fact or inference.
2. Raw provenance with vault name, relative path, and heading, or a clear “no Raw source found”.
3. Confidence analysis covering evidence level, conflicts, dates, and remaining verification work.

If results are empty, say that the knowledge base has no direct evidence and name the searched vaults and layers.

## 3. Upload workflow

Call `inspect_uploaded_file` before choosing a destination. It reads locally, computes a SHA-256, classifies the suffix, and suggests vaults. Do not pick a weak suggestion automatically. Call `plan_file_ingest` only after the vault is selected. The plan contains:

- the original attachment at `raw/YYYY-MM/attachments/`;
- a Raw Markdown note preserving extracted text and provenance;
- an evidence card with claim, entities, topics, evidence level, Raw link, and next action;
- optional Wiki candidates that are suggestions, not edits.

Markdown/text, CSV, JSON, YAML, HTML/XML, and DOCX are extracted with local standard-library code. PDF, image, archive, and unknown binary uploads are `archive_only`; the evidence card remains `待验证` and says that extraction did not occur.

Show every target path, preview, source hash, and evidence level. Only an explicit approval of that exact plan authorizes `apply_vault_plan`. The server refuses stale plans, changed uploads, path traversal, hidden directories, existing targets, and Wiki sections that no longer match their fingerprint.

## 4. Cloud and privacy

The service never enables cloud sync or sends note/upload content to a remote endpoint. Configure cloud sync in Obsidian or your operating system. During development, use a temporary vault and compare a hash manifest of real Markdown files before and after verification.
