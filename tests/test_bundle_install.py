import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kshetrajna.bundle_install import digest, install_bundle, verify_bundle


class BundleInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "download"
        self.source.mkdir()
        members = ("runtime/python.exe", "runtime/pythonw.exe", "Launch.cmd", "Install.cmd",
                   "app/src/kshetrajna/__main__.py", "app/src/kshetrajna/web/index.html")
        for relative in members:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test fixture only")
        self.manifest = {"schema": 1, "application": "Kshetrajna", "version": "0.4.0",
                         "architecture": "x64", "source_commit": "a" * 40,
                         "files": {name: digest(self.source / name) for name in members}}
        self.write_manifest()
        self.programs = self.root / "profile" / "Programs"

    def write_manifest(self):
        (self.source / "bundle.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_verified_install_is_repeatable_and_preserves_history(self):
        history = self.root / "profile" / "Kshetrajna" / "events.db"
        history.parent.mkdir(parents=True)
        history.write_bytes(b"personal history sentinel")
        installed = install_bundle(self.source, self.programs)
        self.assertEqual(verify_bundle(installed), self.manifest)
        self.assertEqual(install_bundle(self.source, self.programs), installed)
        self.assertEqual(history.read_bytes(), b"personal history sentinel")
        self.assertFalse((installed / "events.db").exists())

    def test_corrupt_download_is_rejected_before_install(self):
        (self.source / "runtime" / "python.exe").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "integrity"):
            install_bundle(self.source, self.programs)
        self.assertFalse(self.programs.exists())

    def test_traversal_and_windows_paths_are_rejected(self):
        for name in ("../outside.txt", "/outside.txt", "C:/outside.txt", "runtime\\outside.txt", "runtime/./python.exe"):
            with self.subTest(name=name):
                self.manifest["files"][name] = "0" * 64
                self.write_manifest()
                with self.assertRaises(ValueError):
                    install_bundle(self.source, self.programs)
                self.manifest["files"].pop(name)
        self.assertFalse(self.programs.exists())

    def test_copy_corruption_never_becomes_an_installation(self):
        import shutil
        original_copy = shutil.copyfile
        def corrupt_copy(source, destination):
            result = original_copy(source, destination)
            if Path(destination).name == "python.exe":
                Path(destination).write_bytes(b"copy race")
            return result
        with patch("kshetrajna.bundle_install.shutil.copyfile", side_effect=corrupt_copy):
            with self.assertRaisesRegex(ValueError, "integrity"):
                install_bundle(self.source, self.programs)
        self.assertEqual(list((self.programs / "Kshetrajna").iterdir()), [])

    def test_new_version_preserves_previous_installation(self):
        previous = install_bundle(self.source, self.programs)
        self.manifest["source_commit"] = "b" * 40
        self.manifest["version"] = "0.4.1"
        self.write_manifest()
        current = install_bundle(self.source, self.programs)
        self.assertNotEqual(current, previous)
        self.assertTrue(previous.is_dir())
        self.assertEqual(verify_bundle(previous)["version"], "0.4.0")

    def test_existing_install_is_not_overwritten_if_damaged(self):
        installed = install_bundle(self.source, self.programs)
        marker = installed / "runtime" / "python.exe"
        marker.write_bytes(b"preserve damaged installation for review")
        with self.assertRaisesRegex(ValueError, "integrity"):
            install_bundle(self.source, self.programs)
        self.assertEqual(marker.read_bytes(), b"preserve damaged installation for review")

    def test_symlinked_payload_is_rejected(self):
        member = self.source / "runtime" / "python.exe"
        target = self.root / "outside.exe"
        target.write_bytes(member.read_bytes())
        member.unlink()
        try:
            member.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this host")
        with self.assertRaisesRegex(ValueError, "links"):
            install_bundle(self.source, self.programs)
        self.assertFalse(self.programs.exists())

    def test_case_colliding_inventory_is_rejected(self):
        self.manifest["files"]["RUNTIME/PYTHON.EXE"] = self.manifest["files"]["runtime/python.exe"]
        self.write_manifest()
        with self.assertRaises(ValueError):
            verify_bundle(self.source)
