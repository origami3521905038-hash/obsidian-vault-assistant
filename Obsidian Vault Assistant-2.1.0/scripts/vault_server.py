#!/usr/bin/env python3
"""MCP server for safe, tiered retrieval and structured Obsidian updates.

The server discovers vaults under a configurable root (iCloud by default),
retrieves curated notes before raw material, and makes write operations an
explicit two-step transaction: plan first, then apply with confirmation.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from defusedxml import ElementTree as SafeET
except ImportError:  # pragma: no cover - the fallback rejects risky XML constructs first
    SafeET = None

try:
    from environment import apply_environment_setup, check_environment, plan_environment_setup
except ImportError:  # pragma: no cover - supports importing this file by path
    from scripts.environment import apply_environment_setup, check_environment, plan_environment_setup


ICLOUD_OBSIDIAN_ROOT = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Obsidian"
LOCAL_OBSIDIAN_ROOTS = [Path.home() / "Documents" / "Obsidian", Path.home() / "Obsidian"]
IGNORED_PARTS = {".obsidian", ".git", ".trash", "node_modules", "__pycache__", ".DS_Store"}
MAX_NOTE_BYTES = 2_000_000
MAX_UPLOAD_BYTES = 25_000_000
MAX_EXTRACTED_TEXT_CHARS = 1_800_000
MAX_PLAN_AGE_SECONDS = 30 * 60
PENDING_PLANS: dict[str, dict[str, Any]] = {}
TEXT_UPLOAD_SUFFIXES = {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml", ".html", ".htm", ".xml"}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _should_ignore(path: Path) -> bool:
    return any(part in IGNORED_PARTS or part.startswith(".") for part in path.parts)


def _note_paths(vault: Path):
    resolved_vault = vault.resolve()
    for path in vault.rglob("*.md"):
        if _should_ignore(path):
            continue
        # rglob returns symlink entries. Canonicalize before yielding so a
        # link cannot make enumeration read outside the selected vault.
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not _is_within(resolved, resolved_vault) or not resolved.is_file():
            continue
        yield resolved


def _has_note(vault: Path) -> bool:
    return next(_note_paths(vault), None) is not None


def _has_direct_note(vault: Path) -> bool:
    try:
        return any(path.is_file() and path.suffix.lower() == ".md" for path in vault.iterdir())
    except OSError:
        return False


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result = []
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in seen and path.is_dir():
            seen.add(resolved)
            result.append(path.resolve())
    return result


def _conventional_roots() -> list[Path]:
    roots = [ICLOUD_OBSIDIAN_ROOT, *LOCAL_OBSIDIAN_ROOTS]
    one_drive = os.environ.get("OneDrive", "").strip()
    if one_drive:
        roots.append(Path(one_drive) / "Documents" / "Obsidian")
    return roots


def get_vault_roots() -> list[Path]:
    """Return explicitly configured roots first, then conventional locations."""
    root_value = os.environ.get("OBSIDIAN_VAULT_ROOT", "").strip()
    if root_value:
        return _unique_paths([Path(part).expanduser() for part in root_value.split(os.pathsep) if part])
    exact_vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    if exact_vault:
        exact_path = Path(exact_vault).expanduser()
        if exact_path.is_dir() and _has_note(exact_path):
            return _unique_paths([exact_path])
    values = [str(path) for path in _conventional_roots()]
    return _unique_paths([Path(value).expanduser() for value in values])


def _configured_or_default_creation_roots() -> list[Path]:
    configured = os.environ.get("OBSIDIAN_VAULT_ROOT", "").strip()
    if configured:
        return [Path(part).expanduser().resolve() for part in configured.split(os.pathsep) if part]
    return [(Path.home() / "Documents" / "Obsidian").resolve()]


def discover_vaults() -> list[dict[str, Any]]:
    """Find top-level vaults that actually contain Markdown notes.

    An iCloud Obsidian directory often has no single vault marker: each
    top-level folder may be an independent vault. We therefore discover by
    content as well as by .obsidian markers, but do not treat metadata folders
    as vaults.
    """
    candidates: list[Path] = []
    explicit_vaults = []
    exact_vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    if os.environ.get("OBSIDIAN_VAULT_ROOT", "").strip():
        exact_vault = ""
    if exact_vault:
        exact_path = Path(exact_vault).expanduser()
        if exact_path.is_dir() and _has_note(exact_path):
            explicit_vaults.append(exact_path.resolve())
            candidates.append(exact_path.resolve())
    for root in get_vault_roots():
        # A configured vault is a leaf, not a library root. Its wiki/raw
        # folders must never be discovered as separate vaults.
        if root in explicit_vaults:
            continue
        if (root / ".obsidian").is_dir() and (_has_direct_note(root) or (root / "Home.md").is_file() or (root / "index.md").is_file()):
            candidates.append(root)
            continue
        direct_notes = any(path.parent == root.resolve() for path in _note_paths(root))
        if direct_notes:
            candidates.append(root)
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and not _should_ignore(child) and _has_note(child):
                candidates.append(child)
        # Support a user-organized iCloud root with nested vaults identified
        # by Obsidian's marker directory, without treating ordinary note
        # subfolders as independent vaults.
        try:
            for marker in root.rglob(".obsidian"):
                candidate = marker.parent
                if candidate != root and not _should_ignore(marker) and _has_note(candidate):
                    candidates.append(candidate)
        except OSError:
            pass

    vaults = []
    for path in _unique_paths(candidates):
        notes = list(_note_paths(path))
        vaults.append({
            "name": path.name,
            "path": str(path),
            "note_count": len(notes),
            "icloud": _is_within(path, ICLOUD_OBSIDIAN_ROOT),
            "has_obsidian_config": (path / ".obsidian").is_dir(),
        })
    return sorted(vaults, key=lambda item: (item["name"].casefold(), item["path"]))


def _resolve_vault_path(value: str) -> Path:
    target = Path(value).expanduser().resolve()
    if not target.is_dir():
        raise ValueError(f"Vault not found: {value}")
    roots = get_vault_roots()
    if not any(_is_within(target, root) for root in roots):
        raise PermissionError("Vault path is outside configured Obsidian roots.")
    if not _has_note(target):
        raise ValueError(f"Vault has no Markdown notes: {target}")
    return target


def select_vaults(arguments: dict[str, Any], require_one: bool = False) -> list[Path]:
    explicit_path = str(arguments.get("vault_path", "")).strip()
    if explicit_path:
        selected = [_resolve_vault_path(explicit_path)]
    else:
        name = str(arguments.get("vault_name", "")).strip()
        vaults = discover_vaults()
        if name:
            selected = [Path(item["path"]) for item in vaults if item["name"] == name]
            if not selected:
                raise ValueError(f"No discovered vault is named '{name}'. Call list_vaults first.")
            if len(selected) > 1:
                raise ValueError(f"Vault name '{name}' is ambiguous. Use vault_path instead.")
        else:
            selected = [Path(item["path"]) for item in vaults]
    if not selected:
        raise ValueError("No Obsidian vaults found. Set OBSIDIAN_VAULT_ROOT to the directory containing your vaults.")
    if require_one and len(selected) != 1:
        raise ValueError("This operation requires one vault. Pass vault_path from list_vaults.")
    return selected


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_NOTE_BYTES:
        raise ValueError(f"Note is larger than {MAX_NOTE_BYTES} bytes and was skipped: {path.name}")
    return path.read_text(encoding="utf-8", errors="replace")


def _relative_note_path(vault: Path, file_path: str) -> Path:
    if not file_path:
        raise ValueError("file_path is required.")
    target = (vault / file_path).resolve()
    if not _is_within(target, vault):
        raise PermissionError(f"Access denied: {file_path} is outside the selected vault.")
    relative = target.relative_to(vault.resolve())
    if _should_ignore(relative):
        raise PermissionError(f"Access denied: {file_path} is in a protected directory.")
    if not target.is_file() or target.suffix.lower() != ".md":
        raise ValueError(f"Note not found: {file_path}")
    return target


def list_notes(vault: Path) -> list[dict[str, Any]]:
    notes = []
    for path in _note_paths(vault):
        stat = path.stat()
        notes.append({
            "path": str(path.relative_to(vault.resolve())),
            "name": path.stem,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "tier": note_tier(path.relative_to(vault.resolve())),
        })
    return sorted(notes, key=lambda item: item["path"].casefold())


def read_note(vault: Path, file_path: str) -> str:
    return _read_text(_relative_note_path(vault, file_path))


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---"):
        return {}, content
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", content, flags=re.DOTALL)
    if not match:
        return {}, content
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data, content[match.end():]


def _heading_entries(body: str) -> list[dict[str, Any]]:
    lines = body.splitlines(keepends=True)
    headings = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append({"index": index, "level": len(match.group(1)), "heading": match.group(2).strip()})
    # Resolve section boundaries in one forward pass. The previous suffix
    # scan made a note with many headings quadratic to parse.
    ends = [len(lines)] * len(headings)
    open_headings: list[int] = []
    for position, item in enumerate(headings):
        while open_headings and item["level"] <= headings[open_headings[-1]]["level"]:
            ends[open_headings.pop()] = item["index"]
        open_headings.append(position)

    entries = []
    for position, item in enumerate(headings):
        end = ends[position]
        section = "".join(lines[item["index"]:end]).strip()
        entries.append({
            "heading": item["heading"],
            "level": item["level"],
            "line_start": item["index"] + 1,
            "line_end": end,
            "content": section,
        })
    if not entries:
        entries.append({"heading": "Document", "level": 0, "line_start": 1, "line_end": len(lines), "content": body.strip()})
    return entries


def note_tier(relative_path: Path) -> str:
    parts = [part.casefold() for part in relative_path.parts]
    first = parts[0] if parts else ""
    raw_names = {"raw", "raw_data", "raw-data", "99-原始素材", "06-原始素材", "原始素材"}
    middle_names = {"00_inbox", "00-inbox", "inbox", "evidence", "evidence-cards", "facts", "cards", "09_sources", "09-sources", "来源索引"}
    if first in raw_names or any(part in raw_names for part in parts[:-1]):
        return "raw"
    if first in middle_names or any(part in middle_names for part in parts[:-1]):
        return "middle"
    if first in {"templates", "ops", "_meta"}:
        return "system"
    return "wiki"


def _query_terms(query: str) -> list[str]:
    cleaned = query.casefold().strip()
    terms: set[str] = {cleaned} if cleaned else set()
    terms.update(token for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", cleaned) if len(token) > 1)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", cleaned):
        terms.add(run)
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return sorted((term for term in terms if term), key=len, reverse=True)


def _snippet(text: str, terms: list[str], limit: int = 260) -> str:
    lowered = text.casefold()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    if not positions:
        return re.sub(r"\s+", " ", text).strip()[:limit]
    position = min(positions)
    start = max(0, position - limit // 3)
    end = min(len(text), position + limit)
    value = re.sub(r"\s+", " ", text[start:end]).strip()
    return ("..." if start else "") + value + ("..." if end < len(text) else "")


def _score_text(text: str, terms: list[str], multiplier: float = 1.0) -> float:
    lowered = text.casefold()
    return sum(min(lowered.count(term), 5) * max(len(term), 1) * multiplier for term in terms)


def _search_candidates(vaults: list[Path], query: str, tiers: set[str], max_results: int) -> list[dict[str, Any]]:
    terms = _query_terms(query)
    if not terms:
        raise ValueError("query is required.")
    results = []
    for vault in vaults:
        for path in _note_paths(vault):
            relative = path.relative_to(vault.resolve())
            tier = note_tier(relative)
            if tier not in tiers:
                continue
            try:
                content = _read_text(path)
            except (OSError, ValueError):
                continue
            metadata, body = _parse_frontmatter(content)
            title_score = _score_text(path.stem, terms, 9)
            metadata_score = _score_text(" ".join(metadata.values()), terms, 4)
            sections = _heading_entries(body)
            best = max(
                sections,
                key=lambda section: _score_text(section["heading"], terms, 6) + _score_text(section["content"], terms),
            )
            section_score = _score_text(best["heading"], terms, 6) + _score_text(best["content"], terms)
            score = title_score + metadata_score + section_score
            if score <= 0:
                continue
            evidence = metadata.get("evidence_level", metadata.get("evidence", ""))
            results.append({
                "vault_name": vault.name,
                "vault_path": str(vault),
                "path": str(relative),
                "name": path.stem,
                "tier": tier,
                "heading": best["heading"],
                "line_start": best["line_start"],
                "line_end": best["line_end"],
                "evidence_level": evidence or None,
                "score": round(score, 2),
                "snippet": _snippet(best["content"], terms),
            })
    results.sort(key=lambda item: (-item["score"], item["vault_name"].casefold(), item["path"].casefold()))
    return results[:max(1, min(max_results, 50))]


def search_tiered(vaults: list[Path], query: str, scope: str = "auto", max_results: int = 8, verify_with_raw: bool = False) -> dict[str, Any]:
    scope = scope.casefold().strip() or "auto"
    aliases = {"intermediate": "middle", "structured": "middle"}
    scope = aliases.get(scope, scope)
    valid = {"auto", "all", "raw", "middle", "wiki"}
    if scope not in valid:
        raise ValueError(f"scope must be one of {sorted(valid)}")

    raw_consulted = False
    if scope == "auto":
        results = _search_candidates(vaults, query, {"middle", "wiki"}, max_results)
        if not results or verify_with_raw:
            raw_consulted = True
            raw_results = _search_candidates(vaults, query, {"raw"}, max_results)
            results = (results + raw_results)
            results.sort(key=lambda item: (-item["score"], item["vault_name"].casefold(), item["path"].casefold()))
            results = results[:max(1, min(max_results, 50))]
        plan = {
            "strategy": "middle-and-wiki-first",
            "tiers_searched": ["middle", "wiki"] + (["raw"] if raw_consulted else []),
            "raw_consulted": raw_consulted,
            "raw_reason": "explicit verification requested" if verify_with_raw else ("no structured match" if raw_consulted else "structured results were available"),
        }
    else:
        tiers = {scope} if scope != "all" else {"raw", "middle", "wiki"}
        results = _search_candidates(vaults, query, tiers, max_results)
        plan = {"strategy": "explicit-scope", "tiers_searched": sorted(tiers), "raw_consulted": "raw" in tiers}
    return {
        "query": query,
        "scope": scope,
        "vaults_searched": [{"name": vault.name, "path": str(vault)} for vault in vaults],
        "retrieval_plan": plan,
        "results": results,
        "next_step": "Use read_note_section with vault_path, path, and heading before reading an entire note.",
    }


def read_note_section(vault: Path, file_path: str, heading: str, max_chars: int = 12_000) -> dict[str, Any]:
    path = _relative_note_path(vault, file_path)
    content = _read_text(path)
    _, body = _parse_frontmatter(content)
    normalized = re.sub(r"^#+\s*", "", heading).strip().casefold()
    for section in _heading_entries(body):
        if section["heading"].casefold() == normalized:
            text = section["content"]
            limit = max(1, min(int(max_chars), 50_000))
            return {
                "path": str(path.relative_to(vault.resolve())),
                "heading": section["heading"],
                "line_start": section["line_start"],
                "line_end": section["line_end"],
                "truncated": len(text) > limit,
                "content": text[:limit],
            }
    raise ValueError(f"Heading not found in {file_path}: {heading}")


def vault_profile(vault: Path) -> dict[str, Any]:
    notes = list_notes(vault)
    tiers = {tier: sum(1 for note in notes if note["tier"] == tier) for tier in ("raw", "middle", "wiki", "system")}
    navigation = [note["path"] for note in notes if Path(note["path"]).name.casefold() in {"home.md", "index.md", "readme.md"}]
    return {
        "name": vault.name,
        "path": str(vault),
        "note_count": len(notes),
        "tier_counts": tiers,
        "navigation_notes": navigation,
        "recommended_entry": navigation[0] if navigation else None,
    }


def audit_vault_structure(vault: Path) -> dict[str, Any]:
    canonical = {"raw", "evidence", "wiki", "templates"}
    existing = {child.name for child in vault.iterdir() if child.is_dir() and not _should_ignore(child)}
    notes = list_notes(vault)
    with_frontmatter = 0
    for note in notes:
        try:
            metadata, _ = _parse_frontmatter(read_note(vault, note["path"]))
            with_frontmatter += int(bool(metadata))
        except (OSError, ValueError):
            pass
    missing = sorted(canonical - existing)
    return {
        "vault_name": vault.name,
        "vault_path": str(vault),
        "canonical_directories": {name: name in existing for name in sorted(canonical)},
        "tier_counts": vault_profile(vault)["tier_counts"],
        "frontmatter_coverage": {"with_frontmatter": with_frontmatter, "total_notes": len(notes)},
        "findings": ([f"Missing canonical directories: {', '.join(missing)}"] if missing else []) +
                    (["No middle/evidence notes found; staged facts cannot yet be retrieved separately from wiki notes."] if not any(note["tier"] == "middle" for note in notes) else []),
        "next_step": "Call plan_vault_bootstrap to preview missing folders and standard templates. It does not write until apply_vault_plan(confirm=true).",
    }


def _yaml_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _frontmatter(fields: dict[str, Any]) -> str:
    return "---\n" + "\n".join(f"{key}: {_yaml_value(value)}" for key, value in fields.items()) + "\n---\n"


def _safe_name(title: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", title.strip())
    value = value.strip(".-_")[:72]
    if not value:
        raise ValueError("title must contain letters, numbers, or Chinese characters.")
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_planned_path(vault: Path, relative_path: str) -> Path:
    candidate = (vault / relative_path).resolve()
    if not _is_within(candidate, vault):
        raise ValueError(f"Invalid vault note path: {relative_path}")
    relative = candidate.relative_to(vault.resolve())
    if _should_ignore(relative) or candidate.suffix.lower() != ".md":
        raise ValueError(f"Invalid vault note path: {relative_path}")
    return candidate


def _safe_archive_path(vault: Path, relative_path: str) -> Path:
    candidate = (vault / relative_path).resolve()
    if not _is_within(candidate, vault):
        raise ValueError(f"Invalid archive path: {relative_path}")
    relative = candidate.relative_to(vault.resolve())
    if _should_ignore(relative) or not candidate.name or candidate.name in {".", ".."}:
        raise ValueError(f"Invalid archive path: {relative_path}")
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_upload_path(value: str) -> Path:
    if not value:
        raise ValueError("upload_path is required.")
    path = Path(value).expanduser()
    if path.is_symlink():
        raise PermissionError("Symlink uploads are not accepted.")
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Uploaded file not found: {Path(value).name}")
    size = path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"Uploaded file is larger than {MAX_UPLOAD_BYTES} bytes.")
    return path


def _plain_text_upload(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("The file appears to be binary, so text extraction was skipped.")
    return data.decode("utf-8-sig")


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        try:
            info = archive.getinfo("word/document.xml")
        except KeyError as error:
            raise ValueError("The DOCX package has no word/document.xml part.") from error
        if info.file_size > MAX_UPLOAD_BYTES:
            raise ValueError("The DOCX document XML is too large to extract safely.")
        document = archive.read(info)
    # DTDs and entities are not part of the supported WordprocessingML
    # format. Reject them before parsing to prevent expansion on systems where
    # defusedxml is unavailable; use the hardened parser when it is installed.
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", document, flags=re.IGNORECASE):
        raise ValueError("DOCX XML contains prohibited DTD or entity declarations.")
    parser = SafeET if SafeET is not None else ET
    root = parser.fromstring(document)
    paragraphs: list[str] = []
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for paragraph in root.iter(namespace + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(namespace + "t")).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _extract_uploaded_file(upload_path: str, preview_chars: int = 12_000) -> dict[str, Any]:
    """Inspect and locally extract a supported upload without changing a vault."""
    path = _resolve_upload_path(upload_path)
    suffix = path.suffix.casefold()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    status = "supported"
    parser = None
    text = ""
    warning = None
    try:
        if suffix in TEXT_UPLOAD_SUFFIXES:
            parser = "utf-8-text"
            text = _plain_text_upload(path)
        elif suffix == ".docx":
            parser = "docx-xml"
            text = _docx_text(path)
        else:
            status = "archive_only"
            warning = "This file type is not parsed by the standard-library extractor. It can be archived, but no evidence or Wiki content will be invented."
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile, ET.ParseError) as error:
        status = "archive_only"
        parser = None
        text = ""
        warning = f"Text extraction failed safely: {error}"
    limit = max(1, min(int(preview_chars), 50_000))
    return {
        "_source_path": str(path),
        "name": path.name,
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
        "mime_type": mime_type,
        "suffix": suffix,
        "extraction": {
            "status": status,
            "parser": parser,
            "characters": len(text),
            "truncated": len(text) > limit,
            "preview": text[:limit],
            "warning": warning,
        },
        "writes_performed": False,
    }


def inspect_uploaded_file(upload_path: str, vaults: list[Path], preview_chars: int = 12_000) -> dict[str, Any]:
    inspection = _extract_uploaded_file(upload_path, preview_chars)
    extraction = inspection["extraction"]
    query = Path(inspection["name"]).stem
    if extraction["status"] == "supported":
        preview = str(extraction["preview"])
        query = (query + " " + preview[:1200]).strip()
    suggestions: list[dict[str, Any]] = []
    if query:
        matches = _search_candidates(vaults, query, {"middle", "wiki"}, 30)
        grouped: dict[str, dict[str, Any]] = {}
        for match in matches:
            key = match["vault_path"]
            item = grouped.setdefault(key, {"vault_name": match["vault_name"], "vault_path": key, "score": 0.0, "matched_notes": []})
            item["score"] += float(match["score"])
            if len(item["matched_notes"]) < 3:
                item["matched_notes"].append({"path": match["path"], "heading": match["heading"], "score": match["score"]})
        suggestions = sorted(grouped.values(), key=lambda item: (-item["score"], item["vault_name"].casefold()))[:5]
    inspection["vault_suggestions"] = suggestions
    inspection["selection_policy"] = (
        "Use the top suggestion only when its subject match is clear and materially stronger. "
        "Otherwise ask the user to choose a vault; never write based on a weak or ambiguous suggestion."
    )
    inspection.pop("_source_path", None)
    return inspection


def _render_raw_note(title: str, content: str, arguments: dict[str, Any], captured_at: str) -> str:
    fields = {
        "type": "raw",
        "status": "captured",
        "title": title,
        "source_name": str(arguments.get("source_name") or "用户提供"),
        "source_url": str(arguments.get("source_url") or ""),
        "source_date": str(arguments.get("source_date") or ""),
        "captured_at": captured_at,
        "evidence_level": str(arguments.get("evidence_level") or "low"),
        "tags": arguments.get("tags") or [],
    }
    if arguments.get("source_attachment"):
        fields["source_attachment"] = str(arguments["source_attachment"])
        fields["extraction_status"] = str(arguments.get("extraction_status") or "supported")
        fields["source_sha256"] = str(arguments.get("source_sha256") or "")
    return _frontmatter(fields) + f"\n# {title}\n\n## 原始内容\n\n{content.rstrip()}\n"


def _render_evidence_note(title: str, claim: str, raw_path: str, arguments: dict[str, Any], captured_at: str) -> str:
    fields = {
        "type": "evidence",
        "status": "draft",
        "title": title,
        "claim": claim,
        "entities": arguments.get("entities") or [],
        "topics": arguments.get("topics") or [],
        "source_note": raw_path,
        "source_url": str(arguments.get("source_url") or ""),
        "evidence_level": str(arguments.get("evidence_level") or "low"),
        "captured_at": captured_at,
        "related": arguments.get("related") or [],
        "next_action": str(arguments.get("next_action") or "核验并关联到相关 wiki 页面"),
    }
    if arguments.get("source_attachment"):
        fields["source_attachment"] = str(arguments["source_attachment"])
        fields["extraction_status"] = str(arguments.get("extraction_status") or "supported")
    raw_link = raw_path.removesuffix(".md")
    return _frontmatter(fields) + (
        f"\n# {title}\n\n## 事实陈述\n\n{claim.strip()}\n\n"
        f"## 依据\n\n- [[{raw_link}|原始材料]]\n\n"
        "## 判断边界\n\n- 当前为待核验的结构化证据，不自动升级为确定结论。\n"
    )


def _archive_filename(name: str) -> str:
    source = Path(name)
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", source.stem).strip(".-_")[:80] or "upload"
    suffix = re.sub(r"[^0-9A-Za-z.]+", "", source.suffix)[:12]
    return stem + suffix


def plan_file_ingest(vault: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    """Plan archiving an upload and creating its Raw/evidence decomposition."""
    upload_path = str(arguments.get("upload_path") or "").strip()
    inspection = _extract_uploaded_file(upload_path, 2_000)
    extraction = inspection["extraction"]
    source = Path(inspection["_source_path"])
    title = str(arguments.get("title") or source.stem).strip()
    if not title:
        raise ValueError("title must not be empty.")
    claim = str(arguments.get("claim") or "").strip()
    if extraction["status"] == "archive_only" and not claim:
        claim = "未提取：原始文件已归档，需人工查看后再形成事实陈述。"
    if extraction["status"] == "supported" and not claim:
        claim = "待从原始材料提炼并核验；当前没有自动升级为确定结论。"
    evidence_level = str(arguments.get("evidence_level") or ("待验证" if extraction["status"] == "archive_only" else "low")).casefold()
    if evidence_level not in {"low", "medium", "high", "待验证"}:
        raise ValueError("evidence_level must be low, medium, high, or 待验证.")
    date_folder = str(arguments.get("captured_date") or datetime.now(timezone.utc).date().isoformat())[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", date_folder):
        raise ValueError("captured_date must start with YYYY-MM.")
    slug = _safe_name(title)
    archive_relative = f"raw/{date_folder}/attachments/{_archive_filename(source.name)}"
    raw_relative = f"raw/{date_folder}/{slug}.md"
    evidence_relative = f"evidence/{slug}.md"
    archive_target = _safe_archive_path(vault, archive_relative)
    for relative in (archive_relative, raw_relative, evidence_relative):
        target = archive_target if relative == archive_relative else _safe_planned_path(vault, relative)
        if target.exists():
            raise ValueError(f"Planned file already exists: {relative}. Use a more specific title or filename; existing notes are never overwritten.")
    ingest_args = dict(arguments)
    ingest_args.update({
        "source_name": str(arguments.get("source_name") or source.name),
        "source_attachment": archive_relative,
        "source_sha256": inspection["sha256"],
        "extraction_status": extraction["status"],
        "evidence_level": evidence_level,
    })
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    raw_content = str(extraction.get("preview") or "")
    if extraction["status"] == "supported":
        raw_content = _plain_text_upload(source) if source.suffix.casefold() != ".docx" else _docx_text(source)
        if len(raw_content) > MAX_EXTRACTED_TEXT_CHARS:
            raw_content = raw_content[:MAX_EXTRACTED_TEXT_CHARS].rstrip() + "\n\n[提取内容过长；此 Raw 笔记仅保留前段文本，完整原件见 source_attachment。]"
    else:
        raw_content = "原始文件已归档，但当前标准解析器未提取其内容。请打开附件进行人工核验。"
    operations: list[dict[str, Any]] = []
    for directory in (f"raw/{date_folder}", f"raw/{date_folder}/attachments", "evidence"):
        if not (vault / directory).exists():
            operations.append({"action": "mkdir", "directory": directory})
    operations.extend([
        {"action": "copy_file", "source_path": str(source), "source_sha256": inspection["sha256"], "path": archive_relative, "size": inspection["size"]},
        {"action": "create", "path": raw_relative, "content": _render_raw_note(title, raw_content, ingest_args, captured_at)},
        {"action": "create", "path": evidence_relative, "content": _render_evidence_note(title, claim, raw_relative, ingest_args, captured_at)},
    ])
    wiki_targets = arguments.get("wiki_targets") or []
    if not isinstance(wiki_targets, list):
        raise ValueError("wiki_targets must be a list.")
    operations.extend(_validate_wiki_target(vault, item) for item in wiki_targets if isinstance(item, dict))
    if len([item for item in wiki_targets if isinstance(item, dict)]) != len(wiki_targets):
        raise ValueError("Every wiki_targets item must be an object.")
    plan = _store_plan(vault, "file_ingest", operations)
    plan["upload"] = {
        "name": inspection["name"],
        "size": inspection["size"],
        "sha256": inspection["sha256"],
        "mime_type": inspection["mime_type"],
        "extraction": {key: value for key, value in extraction.items() if key != "preview"},
    }
    plan["decomposition"] = {
        "raw_attachment": archive_relative,
        "raw_note": raw_relative,
        "evidence_note": evidence_relative,
        "wiki_candidates": _suggest_wiki_targets(vault, title, claim, ingest_args),
    }
    plan["no_invention_policy"] = "Unsupported or failed extraction creates an archive-only attachment and an explicitly unverified evidence card; no binary content is guessed."
    return plan


def _raw_template() -> str:
    return _frontmatter({"type": "raw", "status": "captured", "title": "", "source_name": "", "source_url": "", "captured_at": "", "evidence_level": "low", "tags": []}) + "\n# 标题\n\n## 原始内容\n"


def _evidence_template() -> str:
    return _frontmatter({"type": "evidence", "status": "draft", "claim": "", "entities": [], "topics": [], "source_note": "", "source_url": "", "evidence_level": "low", "related": [], "next_action": ""}) + "\n# 标题\n\n## 事实陈述\n\n## 依据\n\n## 判断边界\n"


def _wiki_template() -> str:
    return _frontmatter({"type": "wiki", "status": "seed", "tags": [], "evidence_level": "low", "updated": "", "related": [], "next_action": ""}) + "\n# 标题\n\n## 一句话判断\n\n## 事实\n\n## 推断\n\n## 关联页面\n\n## 待验证\n\n## 下一步\n"


def _store_plan(vault: Path, kind: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    plan_id = uuid.uuid4().hex
    PENDING_PLANS[plan_id] = {"vault_path": str(vault), "kind": kind, "created_at": time.time(), "operations": operations}
    files = []
    for operation in operations:
        preview = operation.get("content", "")
        if operation.get("action") == "copy_file":
            preview = f"archive source ({operation.get('size', 0)} bytes, sha256={operation.get('source_sha256', '')})"
        files.append({
            "action": operation["action"],
            "path": operation.get("path", operation.get("directory")),
            "preview": preview[:1600] + ("\n... [truncated]" if len(preview) > 1600 else ""),
        })
    return {
        "plan_id": plan_id,
        "vault_path": str(vault),
        "kind": kind,
        "operations": files,
        "confirmation_required": True,
        "expires_in_seconds": MAX_PLAN_AGE_SECONDS,
        "apply_instruction": "After the user explicitly approves this exact plan, call apply_vault_plan with plan_id and confirm=true.",
    }


def plan_vault_bootstrap(vault: Path) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    for directory in ("raw", "evidence", "wiki", "templates"):
        if not (vault / directory).exists():
            operations.append({"action": "mkdir", "directory": directory})
    templates = {"templates/Raw.md": _raw_template(), "templates/Evidence.md": _evidence_template(), "templates/Wiki.md": _wiki_template()}
    for relative, content in templates.items():
        target = _safe_planned_path(vault, relative)
        if not target.exists():
            operations.append({"action": "create", "path": relative, "content": content})
    return _store_plan(vault, "bootstrap", operations)


def plan_new_vault(arguments: dict[str, Any]) -> dict[str, Any]:
    """Preview a canonical local vault when discovery found none."""
    vault_name = str(arguments.get("vault_name") or "Obsidian Knowledge Base").strip()
    safe_name = _safe_name(vault_name)
    allowed_roots = _configured_or_default_creation_roots()
    root_value = str(arguments.get("root_path") or "").strip()
    root = Path(root_value).expanduser().resolve() if root_value else allowed_roots[0]
    if root not in allowed_roots:
        raise PermissionError("New vault root must be the configured OBSIDIAN_VAULT_ROOT or the default local Documents/Obsidian directory.")
    vault = (root / safe_name).resolve()
    if not _is_within(vault, root) or vault == root:
        raise PermissionError("Invalid new vault path.")
    if vault.exists() and any(vault.iterdir()):
        raise ValueError(f"New vault target is not empty: {vault}")
    operations: list[dict[str, Any]] = [
        {"action": "mkdir", "directory": "."},
        {"action": "mkdir", "directory": "raw"},
        {"action": "mkdir", "directory": "evidence"},
        {"action": "mkdir", "directory": "wiki"},
        {"action": "mkdir", "directory": "templates"},
        {"action": "create", "path": "Home.md", "content": _frontmatter({"type": "home", "status": "active", "title": vault_name}) + f"\n# {vault_name}\n\n## 导航\n\n- [[wiki/]]\n- [[evidence/]]\n- [[raw/]]\n"},
        {"action": "create", "path": "templates/Raw.md", "content": _raw_template()},
        {"action": "create", "path": "templates/Evidence.md", "content": _evidence_template()},
        {"action": "create", "path": "templates/Wiki.md", "content": _wiki_template()},
    ]
    plan = _store_plan(vault, "new_vault", operations)
    PENDING_PLANS[plan["plan_id"]]["creation_root"] = str(root)
    plan["cloud_sync"] = "This is a local folder. Configure iCloud Drive, OneDrive, Syncthing, or another provider yourself if cloud sync is desired."
    return plan


def _validate_wiki_target(vault: Path, item: dict[str, Any]) -> dict[str, Any]:
    relative = str(item.get("file_path") or "").strip()
    section = str(item.get("section") or "").strip()
    content = str(item.get("content") or "").strip()
    target = _safe_planned_path(vault, relative)
    if not target.exists():
        raise ValueError(f"wiki target does not exist: {relative}")
    if note_tier(target.relative_to(vault)) != "wiki":
        raise ValueError(f"wiki target must be a Wiki-layer note: {relative}")
    if not section or not content:
        raise ValueError("Each wiki_targets entry needs file_path, section, and content.")
    existing = _read_text(target)
    available = {entry["heading"].casefold() for entry in _heading_entries(_parse_frontmatter(existing)[1])}
    if section.casefold() not in available:
        raise ValueError(f"Section '{section}' does not exist in {relative}.")
    return {"action": "append_section", "path": relative, "section": section, "content": content, "before_sha256": _sha256_text(existing)}


def _suggest_wiki_targets(vault: Path, title: str, claim: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    query = " ".join([title, claim] + [str(value) for key in ("entities", "topics") for value in (arguments.get(key) or [])])
    candidates = _search_candidates([vault], query, {"wiki"}, 5)
    return [
        {
            "file_path": candidate["path"],
            "section": candidate["heading"],
            "score": candidate["score"],
            "evidence_level": candidate["evidence_level"],
            "reason": "匹配标题、事实主张或实体/主题；仅为候选，不会自动修改既有 Wiki 页面",
        }
        for candidate in candidates
    ]


def plan_structured_ingest(vault: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    title = str(arguments.get("title") or "").strip()
    content = str(arguments.get("content") or "").strip()
    claim = str(arguments.get("claim") or "").strip()
    if not title or not content or not claim:
        raise ValueError("title, content, and claim are required to create a raw note and evidence card.")
    evidence_level = str(arguments.get("evidence_level") or "low").casefold()
    if evidence_level not in {"low", "medium", "high", "待验证"}:
        raise ValueError("evidence_level must be low, medium, high, or 待验证.")
    arguments = dict(arguments)
    arguments["evidence_level"] = evidence_level
    date_folder = str(arguments.get("captured_date") or datetime.now(timezone.utc).date().isoformat())[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", date_folder):
        raise ValueError("captured_date must start with YYYY-MM.")
    slug = _safe_name(title)
    raw_relative = f"raw/{date_folder}/{slug}.md"
    evidence_relative = f"evidence/{slug}.md"
    for relative in (raw_relative, evidence_relative):
        if _safe_planned_path(vault, relative).exists():
            raise ValueError(f"Planned note already exists: {relative}. Use a more specific title; existing notes are never overwritten.")
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    operations: list[dict[str, Any]] = []
    for directory in (f"raw/{date_folder}", "evidence"):
        if not (vault / directory).exists():
            operations.append({"action": "mkdir", "directory": directory})
    operations.extend([
        {"action": "create", "path": raw_relative, "content": _render_raw_note(title, content, arguments, captured_at)},
        {"action": "create", "path": evidence_relative, "content": _render_evidence_note(title, claim, raw_relative, arguments, captured_at)},
    ])
    wiki_targets = arguments.get("wiki_targets") or []
    if not isinstance(wiki_targets, list):
        raise ValueError("wiki_targets must be a list.")
    operations.extend(_validate_wiki_target(vault, item) for item in wiki_targets if isinstance(item, dict))
    if len([item for item in wiki_targets if isinstance(item, dict)]) != len(wiki_targets):
        raise ValueError("Every wiki_targets item must be an object.")
    plan = _store_plan(vault, "structured_ingest", operations)
    plan["suggested_wiki_targets"] = _suggest_wiki_targets(vault, title, claim, arguments)
    plan["wiki_target_policy"] = "Candidates are informational. Add exact file_path/section/content entries to wiki_targets only when the intended Wiki placement is known."
    return plan


def _atomic_replace(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary_path = Path(handle.name)
    os.replace(temporary_path, path)


def _exclusive_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _exclusive_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as source_handle, destination.open("xb") as destination_handle:
        shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
        destination_handle.flush()
        os.fsync(destination_handle.fileno())


def _append_to_section(existing: str, section_name: str, addition: str) -> str:
    _, body = _parse_frontmatter(existing)
    prefix_length = len(existing) - len(body)
    lines = body.splitlines(keepends=True)
    target = section_name.casefold()
    heading_index = None
    level = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match and match.group(2).strip().casefold() == target:
            heading_index, level = index, len(match.group(1))
            break
    if heading_index is None or level is None:
        raise ValueError(f"Section no longer exists: {section_name}")
    insert_at = len(lines)
    for index in range(heading_index + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index])
        if match and len(match.group(1)) <= level:
            insert_at = index
            break
    before = "".join(lines[:insert_at]).rstrip() + "\n\n"
    after = "".join(lines[insert_at:])
    return existing[:prefix_length] + before + addition.strip() + "\n\n" + after.lstrip("\n")


def apply_vault_plan(plan_id: str, confirm: bool) -> dict[str, Any]:
    if confirm is not True:
        raise ValueError("Writes require confirm=true after the user explicitly approves the plan.")
    plan = PENDING_PLANS.get(plan_id)
    if not plan:
        raise ValueError("Unknown plan_id. Create a new plan before applying it.")
    if time.time() - plan["created_at"] > MAX_PLAN_AGE_SECONDS:
        PENDING_PLANS.pop(plan_id, None)
        raise ValueError("Plan expired. Generate a fresh plan so targets can be rechecked.")
    if plan.get("kind") == "new_vault":
        vault = Path(plan["vault_path"]).resolve()
        creation_root = Path(plan.get("creation_root", "")).resolve()
        if not creation_root or not _is_within(vault, creation_root) or vault == creation_root:
            raise PermissionError("Invalid new vault creation target.")
        if vault.exists() and any(vault.iterdir()):
            raise ValueError("New vault target changed and is no longer empty.")
    else:
        vault = _resolve_vault_path(plan["vault_path"])
    operations = plan["operations"]

    # Recheck all targets before any mutation, avoiding stale-plan overwrites.
    for operation in operations:
        action = operation["action"]
        if action == "create" and _safe_planned_path(vault, operation["path"]).exists():
            raise ValueError(f"Plan can no longer be applied; file now exists: {operation['path']}")
        if action == "copy_file":
            source = _resolve_upload_path(str(operation.get("source_path", "")))
            if _sha256_file(source) != operation.get("source_sha256"):
                raise ValueError("Plan can no longer be applied; the uploaded source changed.")
            if _safe_archive_path(vault, operation["path"]).exists():
                raise ValueError(f"Plan can no longer be applied; file now exists: {operation['path']}")
        if action == "append_section":
            target = _safe_planned_path(vault, operation["path"])
            if not target.exists() or _sha256_text(_read_text(target)) != operation["before_sha256"]:
                raise ValueError(f"Plan can no longer be applied; target changed: {operation['path']}")

    applied = []
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    replaced_files: list[tuple[Path, str]] = []
    try:
        for operation in operations:
            action = operation["action"]
            if action == "mkdir":
                target = (vault / operation["directory"]).resolve()
                if not _is_within(target, vault) or _should_ignore(target.relative_to(vault.resolve())):
                    raise PermissionError("Invalid planned directory.")
                if not target.exists():
                    target.mkdir(parents=True, exist_ok=True)
                    created_dirs.append(target)
            elif action == "create":
                target = _safe_planned_path(vault, operation["path"])
                _exclusive_write(target, operation["content"])
                created_files.append(target)
            elif action == "copy_file":
                target = _safe_archive_path(vault, operation["path"])
                _exclusive_copy(_resolve_upload_path(str(operation["source_path"])), target)
                created_files.append(target)
            elif action == "append_section":
                target = _safe_planned_path(vault, operation["path"])
                original = _read_text(target)
                replaced_files.append((target, original))
                _atomic_replace(target, _append_to_section(original, operation["section"], operation["content"]))
            else:
                raise ValueError(f"Unknown planned action: {action}")
            applied.append({"action": action, "path": operation.get("path", operation.get("directory"))})
    except Exception:
        # Restore the selected vault to its pre-apply state if an individual
        # filesystem operation fails after earlier operations succeeded.
        for target, original in reversed(replaced_files):
            try:
                _atomic_replace(target, original)
            except OSError:
                pass
        for target in reversed(created_files):
            try:
                target.unlink()
            except OSError:
                pass
        for target in reversed(created_dirs):
            try:
                target.rmdir()
            except OSError:
                pass
        raise
    PENDING_PLANS.pop(plan_id, None)
    return {"status": "applied", "vault_path": str(vault), "applied": applied}


# ---- MCP wire protocol helpers ----

def read_message() -> dict[str, Any] | None:
    content_length = None
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if line.startswith("Content-Length:"):
            content_length = int(line.split(":", 1)[1].strip())
        elif not line and content_length is not None:
            break
    raw = sys.stdin.read(content_length)
    return json.loads(raw) if raw else None


def send_message(message: dict[str, Any]) -> None:
    data = json.dumps(message, ensure_ascii=False)
    sys.stdout.write(f"Content-Length: {len(data.encode('utf-8'))}\r\n\r\n")
    sys.stdout.write(data)
    sys.stdout.flush()


def send_jsonrpc_response(request_id: Any, result: dict[str, Any]) -> None:
    send_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def send_jsonrpc_error(request_id: Any, code: int, message: str, data: str | None = None) -> None:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    send_message({"jsonrpc": "2.0", "id": request_id, "error": error})


def _selection_properties() -> dict[str, Any]:
    return {
        "vault_path": {"type": "string", "description": "Exact vault path returned by list_vaults. Required for writes and reads when more than one vault exists."},
        "vault_name": {"type": "string", "description": "Unique discovered vault name. Use vault_path if names collide."},
    }


def handle_initialize(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-06-18"),
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "obsidian-vault-mcp", "version": "1.0.0"},
    }


def handle_list_tools(_: dict[str, Any]) -> dict[str, Any]:
    selection = _selection_properties()
    return {"tools": [
        {"name": "check_environment", "description": "Read-only cross-platform check for Python, architecture, Obsidian installation, and candidate vault roots. Never changes vault notes.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "plan_environment_setup", "description": "Preview an optional Obsidian installation command or manual download path. Never executes an installer.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "apply_environment_setup", "description": "Run the exact previewed package-manager command only after explicit confirmation; never writes vault notes.", "inputSchema": {"type": "object", "required": ["plan_id", "confirm"], "properties": {"plan_id": {"type": "string"}, "confirm": {"type": "boolean", "const": True}}}},
        {"name": "list_vaults", "description": "Discover every Markdown Obsidian vault under configured roots, including iCloud Obsidian.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_vault_profile", "description": "Return note and Raw/middle/Wiki tier counts plus entry notes for a vault.", "inputSchema": {"type": "object", "properties": selection}},
        {"name": "list_notes", "description": "List Markdown notes and their detected tier. Without a selector, lists notes across all discovered vaults.", "inputSchema": {"type": "object", "properties": selection}},
        {"name": "read_note", "description": "Read a complete Markdown note from one selected vault.", "inputSchema": {"type": "object", "required": ["file_path"], "properties": {**selection, "file_path": {"type": "string"}}}},
        {"name": "read_note_section", "description": "Read one heading section instead of a complete note.", "inputSchema": {"type": "object", "required": ["file_path", "heading"], "properties": {**selection, "file_path": {"type": "string"}, "heading": {"type": "string"}, "max_chars": {"type": "integer", "default": 12000}}}},
        {"name": "search_notes", "description": "Compatibility full-vault search. Prefer search_tiered for balanced retrieval.", "inputSchema": {"type": "object", "required": ["query"], "properties": {**selection, "query": {"type": "string"}, "max_results": {"type": "integer", "default": 20}}}},
        {"name": "search_tiered", "description": "Search all or selected vaults by Raw, middle/evidence, and Wiki tiers. auto searches middle and Wiki before Raw.", "inputSchema": {"type": "object", "required": ["query"], "properties": {**selection, "query": {"type": "string"}, "scope": {"type": "string", "enum": ["auto", "all", "raw", "middle", "wiki"], "default": "auto"}, "max_results": {"type": "integer", "default": 8}, "verify_with_raw": {"type": "boolean", "default": False}}}},
        {"name": "audit_vault_structure", "description": "Read-only audit of canonical Raw/evidence/Wiki/template structure.", "inputSchema": {"type": "object", "properties": selection}},
        {"name": "plan_new_vault", "description": "Preview creation of a new local canonical three-layer vault when no vault is available. This never writes.", "inputSchema": {"type": "object", "properties": {"vault_name": {"type": "string"}, "root_path": {"type": "string"}}}},
        {"name": "plan_vault_bootstrap", "description": "Preview the missing canonical folders and templates. This never writes.", "inputSchema": {"type": "object", "properties": selection}},
        {"name": "plan_structured_ingest", "description": "Create a no-write plan that captures user data as a Raw note and structured evidence card, with optional verified Wiki section additions.", "inputSchema": {"type": "object", "required": ["title", "content", "claim"], "properties": {**selection, "title": {"type": "string"}, "content": {"type": "string"}, "claim": {"type": "string"}, "source_name": {"type": "string"}, "source_url": {"type": "string"}, "source_date": {"type": "string"}, "captured_date": {"type": "string"}, "evidence_level": {"type": "string", "enum": ["low", "medium", "high", "待验证"], "default": "low"}, "entities": {"type": "array", "items": {"type": "string"}}, "topics": {"type": "array", "items": {"type": "string"}}, "tags": {"type": "array", "items": {"type": "string"}}, "related": {"type": "array", "items": {"type": "string"}}, "next_action": {"type": "string"}, "wiki_targets": {"type": "array", "items": {"type": "object"}}}}},
        {"name": "inspect_uploaded_file", "description": "Read-only classify and locally extract an uploaded file, then suggest likely vaults without writing.", "inputSchema": {"type": "object", "required": ["upload_path"], "properties": {**selection, "upload_path": {"type": "string"}, "preview_chars": {"type": "integer", "default": 12000}}}},
        {"name": "plan_file_ingest", "description": "Plan archiving an uploaded file and decomposing it into Raw attachment, Raw note, evidence card, and optional Wiki candidates. No writes occur until apply_vault_plan confirmation.", "inputSchema": {"type": "object", "required": ["upload_path"], "properties": {**selection, "upload_path": {"type": "string"}, "title": {"type": "string"}, "claim": {"type": "string"}, "source_name": {"type": "string"}, "source_url": {"type": "string"}, "source_date": {"type": "string"}, "captured_date": {"type": "string"}, "evidence_level": {"type": "string", "enum": ["low", "medium", "high", "待验证"]}, "entities": {"type": "array", "items": {"type": "string"}}, "topics": {"type": "array", "items": {"type": "string"}}, "tags": {"type": "array", "items": {"type": "string"}}, "related": {"type": "array", "items": {"type": "string"}}, "next_action": {"type": "string"}, "wiki_targets": {"type": "array", "items": {"type": "object"}}}}},
        {"name": "apply_vault_plan", "description": "Apply a previously previewed plan only after explicit user approval. Rechecks paths and target fingerprints before writing.", "inputSchema": {"type": "object", "required": ["plan_id", "confirm"], "properties": {"plan_id": {"type": "string"}, "confirm": {"type": "boolean", "const": True}}}},
    ]}


def _as_content(value: Any, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}], "isError": is_error}


def handle_call_tool(request: dict[str, Any]) -> dict[str, Any]:
    params = request.get("params", {})
    name = params.get("name", "")
    arguments = params.get("arguments", {})
    try:
        if name == "check_environment":
            return _as_content(check_environment())
        if name == "plan_environment_setup":
            return _as_content(plan_environment_setup())
        if name == "apply_environment_setup":
            return _as_content(apply_environment_setup(str(arguments.get("plan_id", "")), arguments.get("confirm") is True))
        if name == "list_vaults":
            return _as_content(discover_vaults())
        if name == "get_vault_profile":
            vaults = select_vaults(arguments)
            return _as_content([vault_profile(vault) for vault in vaults])
        if name == "list_notes":
            vaults = select_vaults(arguments)
            return _as_content([{"vault_name": vault.name, "vault_path": str(vault), "notes": list_notes(vault)} for vault in vaults])
        if name == "read_note":
            vault = select_vaults(arguments, require_one=True)[0]
            return _as_content(read_note(vault, str(arguments.get("file_path", ""))))
        if name == "read_note_section":
            vault = select_vaults(arguments, require_one=True)[0]
            return _as_content(read_note_section(vault, str(arguments.get("file_path", "")), str(arguments.get("heading", "")), arguments.get("max_chars", 12000)))
        if name in {"search_notes", "search_tiered"}:
            vaults = select_vaults(arguments)
            scope = "all" if name == "search_notes" else str(arguments.get("scope", "auto"))
            return _as_content(search_tiered(vaults, str(arguments.get("query", "")), scope, int(arguments.get("max_results", 20 if name == "search_notes" else 8)), bool(arguments.get("verify_with_raw", False))))
        if name == "audit_vault_structure":
            vault = select_vaults(arguments, require_one=True)[0]
            return _as_content(audit_vault_structure(vault))
        if name == "plan_new_vault":
            return _as_content(plan_new_vault(arguments))
        if name == "plan_vault_bootstrap":
            vault = select_vaults(arguments, require_one=True)[0]
            return _as_content(plan_vault_bootstrap(vault))
        if name == "plan_structured_ingest":
            vault = select_vaults(arguments, require_one=True)[0]
            return _as_content(plan_structured_ingest(vault, arguments))
        if name == "inspect_uploaded_file":
            vaults = select_vaults(arguments)
            return _as_content(inspect_uploaded_file(str(arguments.get("upload_path", "")), vaults, int(arguments.get("preview_chars", 12000))))
        if name == "plan_file_ingest":
            vault = select_vaults(arguments, require_one=True)[0]
            return _as_content(plan_file_ingest(vault, arguments))
        if name == "apply_vault_plan":
            return _as_content(apply_vault_plan(str(arguments.get("plan_id", "")), arguments.get("confirm") is True))
        raise ValueError(f"Unknown tool: {name}")
    except (PermissionError, ValueError, OSError) as error:
        return _as_content(str(error), is_error=True)
    except Exception as error:  # Keep MCP failures structured without leaking tracebacks to users.
        return _as_content(f"Unexpected server error: {error}", is_error=True)


def main() -> None:
    sys.stderr.write("Obsidian Vault MCP ready.\n")
    sys.stderr.flush()
    while True:
        request = read_message()
        if request is None:
            break
        method = request.get("method", "")
        request_id = request.get("id")
        if method == "initialize":
            send_jsonrpc_response(request_id, handle_initialize(request))
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send_jsonrpc_response(request_id, handle_list_tools(request))
        elif method == "tools/call":
            send_jsonrpc_response(request_id, handle_call_tool(request))
        elif request_id is not None:
            send_jsonrpc_error(request_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    main()
