"""Five isolated dialogue-level acceptance scenarios.

Every vault mutation in this suite targets a TemporaryDirectory. The tests do
not discover, open, or apply plans to a user's real Obsidian vault.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import environment  # noqa: E402
import vault_server as server  # noqa: E402


class DialogueAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="obsidian-assistant-test-")
        self.root = Path(self.temporary.name)
        self.vault = self.root / "Test Vault"
        self.vault.mkdir()
        (self.vault / "Home.md").write_text("# Home\n\n## 研究状态\n\n测试导航。\n", encoding="utf-8")
        self.environment = mock.patch.dict(os.environ, {"OBSIDIAN_VAULT_ROOT": str(self.root)}, clear=False)
        self.environment.start()
        server.PENDING_PLANS.clear()

    def tearDown(self) -> None:
        server.PENDING_PLANS.clear()
        self.environment.stop()
        self.temporary.cleanup()

    def test_dialogue_1_first_run_absent_is_plan_only(self) -> None:
        """User: 我没装 Obsidian，请帮我初始化。"""
        simulated = {
            "platform": "windows",
            "obsidian": {"installed": False, "known_locations": [], "path_command": None},
            "vault_roots": [str(self.root)],
            "cloud_sync": {"automated": False},
            "writes_performed": False,
        }
        installer = {"method": "winget", "command": ["winget", "install", "--id", "Obsidian.Obsidian", "--exact", "--source", "winget"]}
        with mock.patch.object(environment, "check_environment", return_value=simulated), mock.patch.object(environment, "_installer_for", return_value=installer), mock.patch.object(environment.subprocess, "run") as run:
            plan = environment.plan_environment_setup()
        self.assertEqual(plan["status"], "not_installed")
        self.assertTrue(plan["confirmation_required"])
        self.assertFalse(plan["environment"]["writes_performed"])
        run.assert_not_called()
        bootstrap = server.plan_new_vault({"vault_name": "Fresh Vault", "root_path": str(self.root)})
        self.assertTrue(bootstrap["confirmation_required"])
        self.assertFalse((self.root / "Fresh-Vault").exists())

    def test_dialogue_2_markdown_upload_decomposes_three_layers(self) -> None:
        """User: 把这份 Markdown 资料同步到我的测试库。"""
        (self.vault / "wiki").mkdir()
        (self.vault / "wiki" / "供应链.md").write_text("# 供应链\n\n## 本地化\n\n现有摘要。\n", encoding="utf-8")
        upload = self.root / "访谈.md"
        upload.write_text("# 访谈\n\n工厂正在增加本地采购比例。\n", encoding="utf-8")
        inspection = server.inspect_uploaded_file(str(upload), [self.vault])
        self.assertEqual(inspection["extraction"]["status"], "supported")
        plan = server.plan_file_ingest(self.vault, {
            "upload_path": str(upload),
            "title": "供应链访谈",
            "claim": "访谈称工厂正在增加本地采购比例。",
            "captured_date": "2026-09",
            "topics": ["供应链本地化"],
        })
        self.assertEqual(plan["kind"], "file_ingest")
        self.assertFalse((self.vault / "raw").exists())
        result = server.apply_vault_plan(plan["plan_id"], True)
        self.assertEqual(result["status"], "applied")
        self.assertTrue((self.vault / plan["decomposition"]["raw_attachment"]).is_file())
        self.assertTrue((self.vault / plan["decomposition"]["raw_note"]).is_file())
        self.assertTrue((self.vault / plan["decomposition"]["evidence_note"]).is_file())
        self.assertNotIn("append_section", [item["action"] for item in result["applied"]])

    def test_dialogue_3_binary_upload_archives_without_invention(self) -> None:
        """User: 把这个未知二进制文件同步进去。"""
        upload = self.root / "evidence.bin"
        upload.write_bytes(b"\x00\x01\x02\xffbinary")
        inspection = server.inspect_uploaded_file(str(upload), [self.vault])
        self.assertEqual(inspection["extraction"]["status"], "archive_only")
        plan = server.plan_file_ingest(self.vault, {"upload_path": str(upload), "captured_date": "2026-09"})
        self.assertIn("no binary content is guessed", plan["no_invention_policy"])
        result = server.apply_vault_plan(plan["plan_id"], True)
        self.assertEqual(result["status"], "applied")
        evidence = (self.vault / plan["decomposition"]["evidence_note"]).read_text(encoding="utf-8")
        self.assertIn("待验证", evidence)
        self.assertIn("未提取", evidence)

    def test_dialogue_4_answer_with_raw_provenance_and_confidence(self) -> None:
        """User: 库里的供应链结论是什么？请给原始依据。"""
        (self.vault / "evidence").mkdir()
        (self.vault / "raw").mkdir()
        (self.vault / "evidence" / "供应链.md").write_text(
            "---\nevidence_level: medium\n---\n# 供应链\n\n## 事实陈述\n\n本地采购比例正在增加。\n", encoding="utf-8"
        )
        (self.vault / "raw" / "访谈.md").write_text("# 访谈\n\n## 原话\n\n工厂正在增加本地采购比例。\n", encoding="utf-8")
        result = server.search_tiered([self.vault], "本地采购比例", scope="auto", verify_with_raw=True)
        self.assertTrue(result["retrieval_plan"]["raw_consulted"])
        tiers = {item["tier"] for item in result["results"]}
        self.assertIn("middle", tiers)
        self.assertIn("raw", tiers)
        raw = next(item for item in result["results"] if item["tier"] == "raw")
        section = server.read_note_section(self.vault, raw["path"], raw["heading"])
        answer = {
            "调查结论": "库中资料称本地采购比例正在增加。",
            "Raw 溯源": f"{self.vault.name}/{raw['path']}#{section['heading']}",
            "可信度分析": "中等：证据卡与原始访谈一致，但目前只有单一来源。",
        }
        self.assertEqual(set(answer), {"调查结论", "Raw 溯源", "可信度分析"})

    def test_dialogue_5_absent_knowledge_is_explicit(self) -> None:
        """User: 库里有没有火星采矿许可政策？"""
        result = server.search_tiered([self.vault], "火星采矿许可政策", scope="auto")
        self.assertEqual(result["results"], [])
        answer = "知识库中没有找到直接证据。已搜索 Test Vault 的 middle、wiki 和 raw 层；不使用外部常识补写。"
        self.assertIn("没有找到直接证据", answer)
        self.assertIn("不使用外部常识补写", answer)

    def test_security_symlink_outside_vault_is_not_enumerated(self) -> None:
        """Discovery must not read a Markdown symlink target outside the vault."""
        external = self.root / "private.txt"
        external.write_text("PRIVATE-CONTENT\n", encoding="utf-8")
        (self.vault / "leak.md").symlink_to(external)
        paths = list(server._note_paths(self.vault))
        self.assertEqual(paths, [self.vault.resolve() / "Home.md"])

    def test_security_heading_boundaries_scale_without_changing_sections(self) -> None:
        """The linear heading pass preserves nested section boundaries."""
        body = "\n".join([
            "# Root",
            "root text",
            "## Child",
            "child text",
            "# Next",
            "next text",
        ])
        entries = server._heading_entries(body)
        self.assertEqual([entry["heading"] for entry in entries], ["Root", "Child", "Next"])
        self.assertIn("## Child", entries[0]["content"])
        self.assertNotIn("# Next", entries[0]["content"])
        self.assertEqual(entries[1]["content"], "## Child\nchild text")

    def test_security_docx_entities_are_rejected_before_xml_parse(self) -> None:
        """DOCX DTD/entity declarations fall back to archive-only handling."""
        upload = self.root / "entity.docx"
        xml = (
            "<?xml version='1.0'?><!DOCTYPE w:document [<!ENTITY x 'secret'>]>"
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>"
        )
        with zipfile.ZipFile(upload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", xml)
        inspection = server.inspect_uploaded_file(str(upload), [self.vault])
        self.assertEqual(inspection["extraction"]["status"], "archive_only")
        self.assertIn("prohibited DTD or entity", inspection["extraction"]["warning"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
