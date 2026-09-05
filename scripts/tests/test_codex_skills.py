"""Offline filesystem behavior tests; no Valor services, credentials, or Redis required.

Run with: python3 -m unittest discover -s scripts/tests -p test_codex_skills.py
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "codex_skills.py"
SPEC = importlib.util.spec_from_file_location("codex_skills", SCRIPT)
skills = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(skills)


class SkillInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "repo"
        self.target = Path(self.temporary.name).resolve() / "user/skills"
        self.source = self.root / ".claude/skills-global/example"
        self.native = self.root / ".agents/skills-global/example"
        self.source.mkdir(parents=True)
        self.native.mkdir(parents=True)
        body = (
            '---\nname: example\ndescription: "Use for an example task."\n---\n'
            "\nPerform the task.\n"
        )
        (self.source / "SKILL.md").write_text(body)
        (self.native / "SKILL.md").write_text(body)
        self.write_inventory()

    def write_inventory(self):
        entries = []
        for scope, base in (("global", "skills-global"), ("project", "skills")):
            for source in (self.root / f".claude/{base}").glob("*/SKILL.md"):
                native = self.root / f".agents/{base}" / source.parent.name
                entries.append(
                    {
                        "name": source.parent.name,
                        "scope": scope,
                        "source": source.relative_to(self.root).as_posix(),
                        "target": native.relative_to(self.root).as_posix(),
                        "source_files": {
                            (source.parent / rel).relative_to(self.root).as_posix(): digest
                            for rel, digest in skills.files(source.parent).items()
                        },
                        "resources": list(skills.files(native)),
                    }
                )
        (self.root / skills.MANIFEST).write_text(json.dumps({"version": 1, "skills": entries}))

    def test_dry_run_makes_no_directory(self):
        result = skills.install(self.root, self.target, dry_run=True)
        self.assertEqual(result["changed"], ["example"])
        self.assertFalse(self.target.exists())

    def test_installs_complete_copy_and_rerun_is_idempotent(self):
        (self.native / "guide.md").write_text("Useful reference")
        self.write_inventory()
        skills.install(self.root, self.target)
        installed = self.target / "example"
        self.assertEqual(skills.files(installed), skills.files(self.native))
        self.assertFalse(installed.is_symlink())
        self.assertEqual(skills.install(self.root, self.target)["changed"], [])

    def test_updates_only_owned_unmodified_copy(self):
        skills.install(self.root, self.target)
        (self.native / "SKILL.md").write_text(
            (self.native / "SKILL.md").read_text() + "New step.\n"
        )
        self.assertEqual(skills.install(self.root, self.target)["changed"], ["example"])
        self.assertEqual(skills.files(self.target / "example"), skills.files(self.native))

    def test_local_edits_are_preserved(self):
        skills.install(self.root, self.target)
        installed = self.target / "example/SKILL.md"
        installed.write_text("My local changes")
        with self.assertRaisesRegex(ValueError, "locally edited"):
            skills.install(self.root, self.target)
        self.assertEqual(installed.read_text(), "My local changes")

    def test_existing_unmanaged_skill_is_preserved(self):
        installed = self.target / "example"
        installed.mkdir(parents=True)
        (installed / "mine.txt").write_text("Unrelated work")
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            skills.install(self.root, self.target)
        self.assertEqual((installed / "mine.txt").read_text(), "Unrelated work")
        self.assertFalse((self.target / skills.STATE_NAME).exists())

    def test_symlink_destination_is_rejected(self):
        self.target.mkdir(parents=True)
        (self.target / "example").symlink_to(self.native, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "not a managed"):
            skills.install(self.root, self.target)
        self.assertTrue((self.target / "example").is_symlink())

    def test_detects_source_drift_and_missing_resource(self):
        (self.native / "guide.md").write_text("Reference")
        self.write_inventory()
        (self.native / "guide.md").unlink()
        (self.source / "SKILL.md").write_text("Changed source")
        errors = skills.check(self.root)["errors"]
        self.assertTrue(any("resources differ" in error for error in errors))
        self.assertTrue(any("source changed" in error for error in errors))
        with self.assertRaisesRegex(ValueError, "Validation failed"):
            skills.install(self.root, self.target)
        self.assertFalse(self.target.exists())

    def test_broken_and_nonportable_links_fail(self):
        guide = self.native / "SKILL.md"
        guide.write_text(guide.read_text() + "[missing](missing.md)\n")
        errors = skills.check(self.root)["errors"]
        self.assertTrue(any("broken link" in error for error in errors))
        (self.root / "outside.md").write_text("Not included in global install")
        guide.write_text(guide.read_text() + "[outside](../../../outside.md)\n")
        self.assertTrue(any("nonportable" in error for error in skills.check(self.root)["errors"]))

    def test_project_skill_is_not_installed_globally(self):
        for prefix in (".claude", ".agents"):
            directory = self.root / prefix / "skills/project-only"
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(
                '---\nname: project-only\ndescription: "Use only in this project."\n---\n\nWork.\n'
            )
        self.write_inventory()
        skills.install(self.root, self.target)
        self.assertFalse((self.target / "project-only").exists())
        self.assertEqual(skills.check(self.root)["project"], 1)


class EbookCleanupTests(unittest.TestCase):
    def test_page_number_removal_preserves_paragraphs(self):
        helper = SCRIPT.parent.parent / ".agents/skills/ebook-ingest/scripts/clean_book.py"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.md"
            source.write_text("# Chapter One\n\n“An exam-\nple.”\n\n42\n\n\nSecond paragraph.\n")
            subprocess.run([sys.executable, str(helper), str(source)], check=True)
            self.assertEqual(
                source.with_suffix(".clean.md").read_text(),
                '# Chapter One\n\n"An example."\n\nSecond paragraph.',
            )


if __name__ == "__main__":
    unittest.main()
