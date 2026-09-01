# Ingestion SOP

## File handling

`inspect_uploaded_file` is read-only. It records file name, size, MIME guess, SHA-256, extraction status, and a bounded preview. Standard-library extraction supports UTF-8 Markdown/text, CSV, JSON, YAML, HTML/XML, and DOCX text. Images, PDFs, archives other than DOCX, and unknown binary files are `archive_only` unless another parser is explicitly added later.

`plan_file_ingest` creates three linked layers after a vault is selected:

- `raw/YYYY-MM/attachments/<original-name>`: an exclusive copy of the original file.
- `raw/YYYY-MM/<title>.md`: extracted text and source metadata, or an explicit archive-only message.
- `evidence/<title>.md`: claim, entities, topics, evidence level, Raw link, and next action.

The plan may also list Wiki candidates. Candidates are suggestions only. Existing Wiki pages are appended only when the caller provides an exact file path, existing section, and reviewed content in `wiki_targets`.

Use `low`, `medium`, `high`, or `待验证` evidence levels. A user assertion or one extracted document is not automatically `high`. For unsupported files, default to `待验证` and do not infer content from the file extension, filename, or binary bytes.

All plan operations are no-write previews. Show the complete target list and ask for explicit approval before calling `apply_vault_plan(confirm=true)`. The apply step checks source and target fingerprints and never overwrites an existing file.
