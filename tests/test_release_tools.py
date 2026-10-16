"""Offline regressions for release trust boundaries; no installer execution."""

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_tools as tools
import release_gate as gate
from build_msi import author_payload
from audit_msi import validate_tables
import audit_msi as msi_audit
from export_diagnostics import redact
import publish_release
import assemble_release_evidence as assembly
from build_windows_bundle import RUNTIMES
import check_msi_upgrade as upgrade
from build_msi import UPGRADE_CODES


class ReleaseToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release-tool-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, members):
        path = self.root / "fixture.zip"
        with zipfile.ZipFile(path, "w") as zipped:
            for name, data in members:
                if isinstance(name, str):
                    member = zipfile.ZipInfo(name)
                    # Keep malformed names on disk instead of sanitizing the fixture.
                    member.filename = name
                    name = member
                zipped.writestr(name, data)
        return path

    def test_source_date_epoch_uses_utc_and_rejects_invalid_dates(self):
        with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1767225600"}):
            self.assertEqual(tools.zip_time(), (2026, 1, 1, 0, 0, 0))
        for value in ("", "-1", "1.1", " 1767225600", "0", "999999999999"):
            with self.subTest(value=value), patch.dict(os.environ, {"SOURCE_DATE_EPOCH": value}):
                with self.assertRaises(ValueError):
                    tools.epoch()

    def test_unsafe_paths_are_rejected_before_extraction(self):
        for name in ("../outside.py", "/absolute.py", "C:/x", "a\\x", "a\\", "a\x00hidden", "a/\x00hidden",
                     "a//b", "a/./b", "AUX.txt", "a. /b", "a./b", "a/COM1.txt"):
            with self.subTest(name=name):
                artifact = self.archive([(name, b"fixture")])
                with zipfile.ZipFile(artifact) as zipped:
                    self.assertEqual(zipped.infolist()[0].orig_filename, name)
                with self.assertRaisesRegex(ValueError, "Unsafe artifact member"):
                    tools.scan_archive(artifact)

        for raw, normalized in (("a\\x", "a/x"), ("a\\", "a/"),
                                ("a\x00hidden", "a"), ("a/\x00hidden", "a/")):
            with self.subTest(raw=raw, normalized=normalized):
                artifact = self.archive([(raw, b"fixture")])
                with zipfile.ZipFile(artifact) as zipped:
                    member = zipped.infolist()[0]
                self.assertEqual(member.orig_filename, raw)
                # Emulate the Windows reader's normalized view on every host.
                member.filename = normalized
                with patch.object(zipfile.ZipFile, "infolist", return_value=[member]), \
                        self.assertRaisesRegex(ValueError, "Unsafe artifact member"):
                    tools.scan_archive(artifact)

    def test_archive_collisions_symlinks_and_credentials_are_rejected(self):
        with self.assertRaises(ValueError):
            tools.scan_archive(self.archive([("App/a.py", b"a"), ("app/A.py", b"b")]))
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        with self.assertRaises(ValueError):
            tools.scan_archive(self.archive([(link, b"target")]))
        with self.assertRaises(ValueError):
            tools.scan_archive(self.archive([("app.py", b"-----BEGIN PRIVATE KEY-----")]))

    def test_personal_state_and_build_artifacts_are_rejected(self):
        for name in ("Kshetrajna/events.db", "Kshetrajna/config.json", "Kshetrajna/.env", "tests/a.py", "app/__pycache__/a.pyc", "Kshetrajna/debug.log"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                tools.scan_archive(self.archive([(name, b"private")]))

    def bundle(self):
        content = b"print('fixture')\n"
        manifest = {"schema": 1, "application": "Kshetrajna", "version": "1.0.0", "architecture": "x64",
                    "source_commit": "a" * 40, "dirty_source": False, "python_version": "3.13.16",
                    "runtime_url": "https://www.python.org/runtime", "runtime_sha256": "b" * 64,
                    "files": {"app/src/kshetrajna/__main__.py": hashlib.sha256(content).hexdigest()}}
        return manifest, [("Kshetrajna/app/src/kshetrajna/__main__.py", content), ("Kshetrajna/bundle.json", json.dumps(manifest))]

    def test_bundle_inventory_requires_exact_contents_and_checksums(self):
        manifest, members = self.bundle()
        self.assertEqual(tools.scan_bundle(self.archive(members)), manifest)
        for changed in (members + [("Kshetrajna/unlisted.py", b"extra")], members[1:],
                        [(members[0][0], b"corrupt"), members[1]]):
            with self.assertRaises(ValueError):
                tools.scan_bundle(self.archive(changed))

    def test_wheel_rejects_extra_distribution_and_dependencies(self):
        wheel = self.root / "kshetrajna-1.0.0-py3-none-any.whl"
        base = [("kshetrajna/web/index.html", b"fixture"), ("kshetrajna-1.0.0.dist-info/METADATA", b"Name: kshetrajna\nVersion: 1.0.0\n")]
        for extra in ([("unrelated.py", b"x")], [("other-1.dist-info/METADATA", b"Name: other")]):
            with zipfile.ZipFile(wheel, "w") as zipped:
                for name, content in base + extra:
                    zipped.writestr(name, content)
            with self.assertRaises(ValueError):
                tools.scan_archive(wheel)
        with zipfile.ZipFile(wheel, "w") as zipped:
            zipped.writestr(base[0][0], base[0][1])
            zipped.writestr(base[1][0], base[1][1] + b"Requires-Dist: remote-package\n")
        with self.assertRaises(ValueError):
            tools.scan_archive(wheel)

    def test_spdx_hashes_actual_files_without_claiming_attestation(self):
        _, members = self.bundle()
        artifact = self.archive(members)
        output = self.root / "inventory.json"
        with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1767225600"}):
            tools.inventory(artifact, output)
        document = json.loads(output.read_text())
        self.assertEqual(document["spdxVersion"], "SPDX-2.3")
        self.assertEqual(len(document["files"]), len(members))
        self.assertEqual(document["packages"][0]["licenseConcluded"], "MIT")
        self.assertEqual(document["packages"][1]["licenseDeclared"], "PSF-2.0")
        self.assertIn("not an exhaustive", document["comment"])
        self.assertEqual(document["files"][0]["checksums"][0]["checksumValue"], hashlib.sha256(members[0][1]).hexdigest())

    def test_final_version_tag_commit_and_clean_tree_are_mandatory(self):
        valid = ("1.0.0", "1.0.0", "v1.0.0", "a" * 40, "a" * 40, False)
        gate.validate_identity(*valid)
        for index, value in ((0, "0.5.0"), (0, "1.0.0-rc1"), (1, "0.5.0"), (2, "v1.0.1"), (4, "b" * 40), (5, True)):
            args = list(valid)
            args[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                gate.validate_identity(*args)

    def test_protected_environment_must_have_independent_reviewers_and_explicit_policy(self):
        valid = {"deployment_branch_policy": {"custom_branch_policies": True},
                 "protection_rules": [{"type": "required_reviewers", "prevent_self_review": True,
                                       "reviewers": [{"type": "User", "reviewer": {"id": 42}}]}]}
        gate.validate_environment(valid)
        for changed in ({}, {**valid, "deployment_branch_policy": None}, {**valid, "protection_rules": []},
                        {**valid, "protection_rules": [{"type": "required_reviewers", "prevent_self_review": False, "reviewers": [{"id": 42}]}]}):
            with self.assertRaises(ValueError):
                gate.validate_environment(changed)

    def test_native_evidence_rejects_stale_commit_wrong_runtime_skips_and_booleans(self):
        valid = {"schema": 1, "source_commit": "a" * 40, "architecture": "arm64", "python": "3.13.16", "runtime": "packaged",
                 "tests": 300, "errors": 0, "failures": 0, "skips": 0, "status": "passed"}
        gate.validate_native(valid, "a" * 40, "arm64", "packaged")
        for key, value in (("source_commit", "b" * 40), ("architecture", "x64"), ("runtime", "source"), ("python", "3.13.3"), ("skips", 1), ("tests", True), ("errors", False), ("status", "skipped")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.validate_native({**valid, key: value}, "a" * 40, "arm64", "packaged")

    def test_final_artifact_set_is_exact(self):
        for arch in ("x64", "arm64"):
            for suffix in (".msi", ".spdx.json", ".evidence.json"):
                (self.root / ("Kshetrajna-1.0.0-" + arch + suffix)).write_bytes(b"fixture")
        self.assertEqual(len(gate.exact_artifacts(self.root, "1.0.0")), 6)
        (self.root / "unsigned.zip").write_bytes(b"never silently publish")
        with self.assertRaises(ValueError):
            gate.exact_artifacts(self.root, "1.0.0")

    def test_msi_authoring_is_stable_user_scoped_and_checks_payload(self):
        manifest, members = self.bundle()
        bundle = self.root / "bundle"
        for name, content in members:
            path = bundle / name.removeprefix("Kshetrajna/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode() if isinstance(content, str) else content)
        first, second = self.root / "first.wxs", self.root / "second.wxs"
        author_payload(bundle, first)
        author_payload(bundle, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        tree = ET.parse(first)
        ns = {"w": "http://wixtoolset.org/schemas/v4/wxs"}
        self.assertTrue(all(r.attrib["Root"] == "HKCU" and r.attrib["KeyPath"] == "yes" for r in tree.findall(".//w:RegistryValue", ns)))
        self.assertFalse(tree.findall(".//w:CustomAction", ns))
        (bundle / "unlisted.py").write_bytes(b"x")
        with self.assertRaises(ValueError):
            author_payload(bundle, first)

    def test_msi_table_audit_blocks_global_actions_and_unlisted_cabinet_files(self):
        expected = {"idFixture": ("app.py", b"fixture")}
        registry = [("1", "Software\\Kshetrajna\\MSI\\x64")]
        validate_tables({"File", "Property", "Registry"}, {}, registry, [["idFixture"]], expected)
        cases = [({"CustomAction"}, {}, registry, [["idFixture"]]),
                 ({"File"}, {"ALLUSERS": "1"}, registry, [["idFixture"]]),
                 ({"File"}, {}, [("2", "Software\\Kshetrajna\\MSI\\x64")], [["idFixture"]]),
                 ({"File"}, {}, registry, [["idUnlisted"]])]
        for tables, properties, keys, files in cases:
            with self.subTest(tables=tables), self.assertRaises(ValueError):
                validate_tables(tables, properties, keys, files, expected)
        validate_tables({"File", "RemoveFile"}, {}, registry, [["idFixture"]], expected,
                        [("", "INSTALLFOLDER", "2")], {"INSTALLFOLDER"})
        for removal in (("*", "INSTALLFOLDER", "2"), ("", "HISTORY", "2"), ("", "INSTALLFOLDER", "1")):
            with self.subTest(removal=removal), self.assertRaises(ValueError):
                validate_tables({"File", "RemoveFile"}, {}, registry, [["idFixture"]], expected, [removal], {"INSTALLFOLDER"})

    def test_msi_table_allowlist_rejects_unapproved_mutation_and_unknown_tables(self):
        expected = {"idFixture": ("app.py", b"fixture")}
        registry = [("1", "Software\\Kshetrajna\\MSI\\x64")]
        baseline = {"File", "Property", "Registry", "MsiDigitalSignature", "MsiDigitalCertificate"}
        validate_tables(baseline, {}, registry, [["idFixture"]], expected)
        for table in ("RemoveRegistry", "MoveFile", "DuplicateFile", "IniFile", "RemoveIniFile",
                      "CustomAction", "ServiceInstall", "ServiceControl", "Environment", "Binary", "Shortcut",
                      "Class", "TypeLib", "SelfReg", "ODBCDriver", "LockPermissions", "UnreviewedFutureTable"):
            with self.subTest(table=table), self.assertRaises(ValueError):
                validate_tables(baseline | {table}, {}, registry, [["idFixture"]], expected)

    def msi_upgrade_contract(self, architecture="x64"):
        family = "{" + UPGRADE_CODES[architecture] + "}"
        properties = {"UpgradeCode": family, "ProductVersion": "1.0.0", "ProductLanguage": "1033",
                      "SecureCustomProperties": "WIX_DOWNGRADE_DETECTED;WIX_UPGRADE_DETECTED"}
        # WiX 7.0.0 ParseMajorUpgradeElement: MigrateFeatures=1, OnlyDetect=2;
        # neither version bound is inclusive with our Package.wxs defaults.
        rows = [[family, "", "1.0.0", "1033", "1", "", "WIX_UPGRADE_DETECTED"],
                [family, "1.0.0", "", "1033", "2", "", "WIX_DOWNGRADE_DETECTED"]]
        return properties, rows

    def test_msi_upgrade_rows_match_wix7_major_upgrade_authoring(self):
        package = ET.parse(ROOT / "packaging/windows/Package.wxs").getroot().find("{*}Package")
        self.assertEqual(package.attrib["Language"], "1033")
        self.assertEqual(package.find("{*}MajorUpgrade").attrib, {
            "DowngradeErrorMessage": "A newer Kshetrajna version is already installed.",
            "Schedule": "afterInstallInitialize", "AllowSameVersionUpgrades": "no"})
        for architecture in ("x64", "arm64"):
            with self.subTest(architecture=architecture):
                properties, rows = self.msi_upgrade_contract(architecture)
                msi_audit.validate_upgrade_rows(properties, rows[::-1], architecture, "1.0.0")

    def test_msi_upgrade_rows_reject_extra_families_duplicates_and_changed_semantics(self):
        for architecture in ("x64", "arm64"):
            properties, rows = self.msi_upgrade_contract(architecture)
            unrelated = ["{CCCCCCCC-1111-2222-3333-444444444444}", *rows[0][1:]]
            for changed in ([], rows[:1], rows + [rows[0]], [rows[0], rows[0]], rows + [unrelated]):
                with self.subTest(architecture=architecture, rows=changed), self.assertRaisesRegex(ValueError, "Upgrade"):
                    msi_audit.validate_upgrade_rows(properties, changed, architecture, "1.0.0")
            mutations = {0: (unrelated[0], "{" + UPGRADE_CODES["arm64" if architecture == "x64" else "x64"] + "}"),
                         1: ("0", "9.0.0"), 2: ("0.5.0", "9.0.0"), 3: ("", "1031", "1033,1031"),
                         4: ("0", "3", "5", "257", "513", "1025", "2049", "-1", ""),
                         5: ("ALL", "Application", "[REMOVE]"), 6: ("REMOVE", "UNRELATED", "")}
            for row in range(2):
                for column, values in mutations.items():
                    for value in values:
                        changed = [r[:] for r in rows]
                        changed[row][column] = value
                        with self.subTest(architecture=architecture, row=row, column=column, value=value), self.assertRaisesRegex(ValueError, "Upgrade"):
                            msi_audit.validate_upgrade_rows(properties, changed, architecture, "1.0.0")

    def test_msi_upgrade_rows_require_authored_identity_and_secure_unset_detection_properties(self):
        properties, rows = self.msi_upgrade_contract()
        for key, value in (("UpgradeCode", "{" + UPGRADE_CODES["arm64"] + "}"), ("ProductVersion", "0.5.0"),
                           ("ProductLanguage", "1031"), ("SecureCustomProperties", ""),
                           ("SecureCustomProperties", "WIX_UPGRADE_DETECTED"),
                           ("SecureCustomProperties", properties["SecureCustomProperties"] + ";REMOVE"),
                           ("SecureCustomProperties", properties["SecureCustomProperties"] + ";WIX_UPGRADE_DETECTED"),
                           ("WIX_UPGRADE_DETECTED", "{CCCCCCCC-1111-2222-3333-444444444444}"),
                           ("WIX_DOWNGRADE_DETECTED", "1")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                msi_audit.validate_upgrade_rows({**properties, key: value}, rows, "x64", "1.0.0")
        for architecture, version in (("x86", "1.0.0"), (None, "1.0.0"), ("x64", ""),
                                      ("x64", "1.0.0.1"), ("x64", "256.0.0"), ("x64", None)):
            with self.subTest(architecture=architecture, version=version), self.assertRaises(ValueError):
                msi_audit.validate_upgrade_rows(properties, rows, architecture, version)

    def test_msi_audit_reads_actual_upgrade_rows_before_cabinet_audit(self):
        _, members = self.bundle()
        archive = self.archive(members)
        properties, rows = self.msi_upgrade_contract()
        queries = {
            "SELECT `Name` FROM `_Tables`": [[name] for name in ("Property", "Registry", "File", "Upgrade")],
            "SELECT `Property`, `Value` FROM `Property`": list(properties.items()),
            "SELECT `Root`, `Key` FROM `Registry`": [],
            "SELECT `File` FROM `File`": [[msi_audit.identifier("file:" + name.removeprefix("Kshetrajna/"))] for name, _ in members],
            "SELECT `Cabinet` FROM `Media`": [],
        }
        upgrade_query = "SELECT `UpgradeCode`, `VersionMin`, `VersionMax`, `Language`, `Attributes`, `Remove`, `ActionProperty` FROM `Upgrade`"
        unrelated = ["{CCCCCCCC-1111-2222-3333-444444444444}", *rows[0][1:]]
        for actual, template, error in ((rows + [unrelated], "x64;1033", "MajorUpgrade"),
                                         ([], "x64;1033", "MajorUpgrade"),
                                         (rows, "Arm64;1033", "metadata"),
                                         (rows, "x64;1033", "Expected one embedded cabinet")):
            queries[upgrade_query] = actual
            queries["SELECT `Name` FROM `_Tables`"] = [[name] for name in ("Property", "Registry", "File", *(["Upgrade"] if actual else []))]
            with self.subTest(rows=actual, template=template), patch.object(msi_audit, "os") as environment, \
                    patch.object(msi_audit, "Database") as database:
                environment.name = "nt"
                database.return_value.query.side_effect = lambda sql, columns: queries[sql]
                database.return_value.summary_string.return_value = template
                with self.assertRaisesRegex(ValueError, error):
                    msi_audit.audit(Path("never-installed-fixture.msi"), archive)
                if actual:
                    database.return_value.query.assert_any_call(upgrade_query, 7)
                database.return_value.close.assert_called_once()

    def test_assembly_regenerates_spdx_when_other_architecture_inventory_is_swapped_in(self):
        for target_architecture, swapped_architecture in (("arm64", "x64"), ("x64", "arm64")):
            with self.subTest(target=target_architecture):
                root = self.root / target_architecture
                root.mkdir()
                archives = {}
                for architecture in ("x64", "arm64"):
                    runtime_name = "runtime/libssl-3.dll" if architecture == "x64" else "runtime/libssl-3-arm64.dll"
                    content = ("synthetic runtime fixture " + architecture).encode()
                    manifest, members = self.bundle()
                    manifest.update(architecture=architecture, runtime_url=RUNTIMES[architecture][0], runtime_sha256=RUNTIMES[architecture][1])
                    manifest["files"][runtime_name] = hashlib.sha256(content).hexdigest()
                    archives[architecture] = root / (architecture + ".zip")
                    with zipfile.ZipFile(archives[architecture], "w") as zipped:
                        zipped.writestr(members[0][0], members[0][1])
                        zipped.writestr("Kshetrajna/" + runtime_name, content)
                        zipped.writestr("Kshetrajna/bundle.json", json.dumps(manifest))
                qualified = root / "qualified"
                qualified.mkdir()
                archive = archives[target_architecture]
                msi = root / "candidate.msi"
                msi.write_bytes(b"fixture only; signature verification mocked in this unit test")
                lifecycle = root / "lifecycle.json"
                lifecycle.write_text(json.dumps({"architecture": target_architecture, "source_commit": "a" * 40,
                                                  "msi_sha256": tools.digest(msi)}))
                for runtime in ("source", "packaged"):
                    (qualified / (runtime + ".json")).write_text(json.dumps({
                        "schema": 1, "source_commit": "a" * 40, "architecture": target_architecture,
                        "python": "3.13.16", "runtime": runtime, "tests": 300, "failures": 0,
                        "errors": 0, "skips": 0, "status": "passed"}))
                (qualified / "reproducible.json").write_text(json.dumps({
                    "status": "passed", "kind": "windows", "architecture": target_architecture,
                    "sha256": tools.digest(archive), "source_commit": "a" * 40, "dirty_source": False}))
                output = root / "evidence.json"
                inventory = root / "candidate.spdx.json"
                with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1767225600", "GITHUB_ACTIONS": "true",
                                              "SIGNING_THUMBPRINT": "1" * 40, "SIGNTOOL_PATH": "fixture-only",
                                              "SIGNTOOL_SHA256": "2" * 64}):
                    tools.inventory(archives[swapped_architecture], qualified / "payload.spdx.json")
                    with patch.object(sys, "argv", ["assemble_release_evidence.py", "--qualified", str(qualified),
                                                    "--lifecycle", str(lifecycle), "--msi", str(msi), "--archive", str(archive),
                                                    "--output", str(output), "--inventory", str(inventory)]), \
                            patch.object(assembly, "candidate", return_value={"version": "1.0.0", "source_commit": "a" * 40}), \
                            patch.object(assembly, "audit", return_value={"status": "passed", "msi_sha256": tools.digest(msi),
                                                                         "payload_sha256": tools.digest(archive)}), \
                            patch.object(assembly.subprocess, "run"):
                        assembly.main()
                document = json.loads(inventory.read_text())
                actual_hashes = {f["fileName"]: f["checksums"][0]["checksumValue"] for f in document["files"]}
                expected_hashes = {"./" + name: hashlib.sha256(content).hexdigest()
                                   for name, content in tools.scan_archive(archive).items()}
                self.assertEqual(actual_hashes, expected_hashes)
                runtime = next(p for p in document["packages"] if p["SPDXID"] == "SPDXRef-CPython")
                self.assertEqual(runtime["downloadLocation"], RUNTIMES[target_architecture][0])
                self.assertEqual(runtime["checksums"][0]["checksumValue"], RUNTIMES[target_architecture][1])
                installer = next(p for p in document["packages"] if p["SPDXID"] == "SPDXRef-Installer")
                self.assertEqual(installer["checksums"][0]["checksumValue"], tools.digest(msi))

    def upgrade_identities(self, architecture="x64"):
        predecessor = {"product_code": "{AAAAAAAA-1111-2222-3333-444444444444}",
                       "upgrade_code": "{" + UPGRADE_CODES[architecture] + "}",
                       "architecture": architecture, "per_user": True, "version": "0.5.0"}
        candidate = {**predecessor, "product_code": "{BBBBBBBB-1111-2222-3333-444444444444}", "version": "1.0.0"}
        return predecessor, candidate

    def test_upgrade_identity_reads_platform_and_family_from_msi_metadata(self):
        for architecture, template in (("x64", "x64;1033"), ("arm64", "Arm64;1033")):
            with self.subTest(architecture=architecture):
                predecessor, _ = self.upgrade_identities(architecture)
                properties = {"ProductCode": predecessor["product_code"], "UpgradeCode": predecessor["upgrade_code"],
                              "ProductVersion": predecessor["version"]}
                with patch.object(upgrade, "Database") as database:
                    database.return_value.query.return_value = list(properties.items())
                    database.return_value.summary_string.return_value = template
                    self.assertEqual(upgrade.read_identity(Path("never-installed-fixture.msi")), predecessor)
                    database.return_value.summary_string.assert_called_once_with(7)
                    database.return_value.close.assert_called_once()

    def test_upgrade_preflight_rejects_wrong_architecture_family_product_and_version(self):
        for architecture in ("x64", "arm64"):
            predecessor, candidate = self.upgrade_identities(architecture)
            upgrade.validate_upgrade(predecessor, candidate, architecture)
            for field, value in (("architecture", "arm64" if architecture == "x64" else "x64"),
                                 ("upgrade_code", "{CCCCCCCC-1111-2222-3333-444444444444}"),
                                 ("product_code", candidate["product_code"]), ("per_user", False),
                                 ("product_code", "not-a-product-code"), ("version", "1.0.0"),
                                 ("version", "1.0.1"), ("version", "0.5.0-rc1")):
                with self.subTest(architecture=architecture, field=field, value=value), self.assertRaises(ValueError):
                    upgrade.validate_upgrade({**predecessor, field: value}, candidate, architecture)
            # Matching each other is insufficient: both must match the authored architecture family.
            with self.assertRaisesRegex(ValueError, "UpgradeCode"):
                upgrade.validate_upgrade({**predecessor, "upgrade_code": "{CCCCCCCC-1111-2222-3333-444444444444}"},
                                         {**candidate, "upgrade_code": "{CCCCCCCC-1111-2222-3333-444444444444}"}, architecture)
            with self.assertRaisesRegex(ValueError, "architecture"):
                upgrade.validate_upgrade(predecessor, {**candidate, "architecture": "x86"}, architecture)

    def test_upgrade_registration_requires_old_product_gone_and_new_product_removed(self):
        for phase, states in (("clean", (-1, -1)), ("predecessor", (5, -1)),
                              ("upgraded", (-1, 5)), ("uninstalled", (-1, -1))):
            upgrade.validate_registration(*states, phase)
        # Side-by-side registration must not be certified as an upgrade.
        with self.assertRaisesRegex(ValueError, "predecessor ProductCode must be unregistered"):
            upgrade.validate_registration(5, 5, "upgraded")
        with self.assertRaisesRegex(ValueError, "candidate ProductCode must be unregistered"):
            upgrade.validate_registration(-1, 5, "uninstalled")
        for state in (-7, -6, -5, -4, -3, -2, 0, 1, 2, 3, 4, 5, False, "-1"):
            with self.subTest(state=state), self.assertRaises(ValueError):
                upgrade.validate_registration(state, -1, "uninstalled")
        with self.assertRaisesRegex(ValueError, "installed for the current user"):
            upgrade.validate_registration(-1, -1, "upgraded")

    def test_diagnostic_export_never_copies_free_text_or_private_context(self):
        private = "C:\\Users\\Private Person\\Secret.txt"
        raw = {"device": {"native_machine": "ARM64", "os_version": private, "python": private, "hostname": private},
               "checks": {"sqlite": True, private: True, "python_3_13": private},
               "live_telemetry": {"status": "failed", "error": private,
                                  "collection": {"median_ms": 1.5, "p95_ms": float("nan"), "max_ms": private}},
               "logs": private, "paths": [private], "token": private}
        document = redact(raw)
        serialized = json.dumps(document, allow_nan=False)
        self.assertNotIn(private, serialized)
        self.assertNotIn("os_version", serialized)
        self.assertEqual(document["collection"], {"median_ms": 1.5})
        self.assertEqual(document["device"], {"native_machine": "ARM64"})
        self.assertEqual(document["checks"], {"sqlite": True})

    def test_final_manifest_binds_both_native_results_and_spdx_to_signed_bytes(self):
        directory = self.root / "final"
        directory.mkdir()
        identity = {"version": "1.0.0", "tag": "v1.0.0", "source_commit": "a" * 40, "python": "3.13.16"}
        for architecture in ("x64", "arm64"):
            base = "Kshetrajna-1.0.0-" + architecture
            msi = directory / (base + ".msi")
            msi.write_bytes(b"synthetic artifact, no signing claim outside this test")
            checksum = tools.digest(msi)
            native = {"schema": 1, "source_commit": "a" * 40, "architecture": architecture, "python": "3.13.16",
                      "tests": 300, "failures": 0, "errors": 0, "skips": 0, "status": "passed"}
            evidence = {"source": {**native, "runtime": "source"}, "packaged": {**native, "runtime": "packaged"},
                        "lifecycle": {"schema": 1, "source_commit": "a" * 40, "architecture": architecture,
                                      "status": "passed", "standard_user": True, "history_preserved": True,
                                      "install": "passed", "upgrade": "passed", "uninstall": "passed", "msi_sha256": checksum},
                        "authenticode": "verified", "reproducible_unsigned": True, "content_scan": "passed",
                        "msi_sha256": checksum, "unsigned_payload_sha256": "b" * 64}
            (directory / (base + ".evidence.json")).write_text(json.dumps(evidence))
            inventory = {"spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "packages": [
                {"SPDXID": "SPDXRef-Installer", "name": base + ".msi", "versionInfo": "1.0.0",
                 "checksums": [{"algorithm": "SHA256", "checksumValue": checksum}]}]}
            (directory / (base + ".spdx.json")).write_text(json.dumps(inventory))
        gate.manifest(directory, identity)
        result = json.loads((directory / "release-manifest.json").read_text())
        self.assertEqual(len(result["artifacts"]), 6)
        self.assertIn("not asserted", result["provenance"].lower())
        self.assertIn("release-manifest.json", (directory / "SHA256SUMS").read_text())
        (directory / "release-manifest.json").unlink()
        (directory / "SHA256SUMS").unlink()
        msi.write_bytes(b"changed after native lifecycle")
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            gate.manifest(directory, identity)

    def publication_fixture(self):
        directory = self.root / "publish"
        directory.mkdir()
        (directory / "fixture.msi").write_bytes(b"test fixture")
        (directory / "release-manifest.json").write_text(json.dumps({"artifacts": {"fixture.msi": tools.digest(directory / "fixture.msi")}}))
        (directory / "SHA256SUMS").write_text("fixture only")
        return directory

    def test_publication_never_creates_release_when_real_attestation_verification_fails(self):
        directory = self.publication_fixture()
        commands = []
        def fail(command, **kwargs):
            commands.append(command)
            raise subprocess.CalledProcessError(1, command)
        with patch.object(sys, "argv", ["publish_release.py", "--directory", str(directory)]), \
                patch.object(publish_release, "candidate", return_value={"tag": "v1.0.0", "source_commit": "a" * 40}), \
                patch.object(publish_release, "protected_environment"), \
                patch.dict(os.environ, {"GITHUB_REPOSITORY": "fixture/repository"}), \
                patch.object(publish_release.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                publish_release.main()
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][:3], ["gh", "attestation", "verify"])

    def test_partial_draft_upload_is_never_promoted_to_final_release(self):
        directory = self.publication_fixture()
        commands = []
        def record(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)
        remote = {"isDraft": True, "assets": [{"name": p.name} for p in directory.iterdir()]}
        with patch.object(sys, "argv", ["publish_release.py", "--directory", str(directory)]), \
                patch.object(publish_release, "candidate", return_value={"tag": "v1.0.0", "source_commit": "a" * 40}), \
                patch.object(publish_release, "protected_environment"), \
                patch.dict(os.environ, {"GITHUB_REPOSITORY": "fixture/repository", "GH_TOKEN": "fixture-token"}), \
                patch.object(publish_release.subprocess, "run", side_effect=record), \
                patch.object(publish_release.subprocess, "check_output", return_value=json.dumps(remote)), \
                patch.object(publish_release.urllib.request, "urlopen", side_effect=urllib.error.HTTPError("https://api.github.com/fixture", 404, "fixture", {}, None)):
            with self.assertRaisesRegex(ValueError, "Downloaded draft"):
                publish_release.main()
        self.assertTrue(any(c[:3] == ["gh", "release", "create"] and "--draft" in c for c in commands))
        self.assertFalse(any(c[:3] == ["gh", "release", "edit"] for c in commands))


if __name__ == "__main__":
    unittest.main()
