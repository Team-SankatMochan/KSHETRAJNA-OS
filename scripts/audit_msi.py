"""Read MSI tables and embedded cabinets without installing; match every payload byte."""

import argparse
import ctypes
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from build_msi import UPGRADE_CODES, identifier
from release_tools import check_python, digest, scan_bundle, scan_content


# Only the tables emitted by our file/HKCU/empty-directory authoring and signing
# contract are permitted. New installer capabilities require an explicit audit.
PERMITTED_TABLES = frozenset({
    "_Validation", "Property", "Directory", "Component", "Feature", "FeatureComponents",
    "File", "MsiFileHash", "Media", "Registry", "RemoveFile", "Upgrade", "LaunchCondition",
    "InstallExecuteSequence", "InstallUISequence", "AdminExecuteSequence", "AdminUISequence",
    "AdvtExecuteSequence", "Error", "MsiDigitalSignature", "MsiDigitalCertificate",
})


def validate_tables(tables, properties, registry, file_rows, expected, removals=(), allowed_directories=()):
    unexpected = set(tables) - PERMITTED_TABLES
    if unexpected:
        raise ValueError("Unapproved MSI tables: " + ", ".join(sorted(unexpected)))
    if properties.get("ALLUSERS", "") or properties.get("MSIINSTALLPERUSER", "1") != "1":
        raise ValueError("MSI must be per-user")
    if any(root != "1" or not key.startswith("Software\\Kshetrajna\\MSI\\") for root, key in registry):
        raise ValueError("MSI registry changes must be narrowly scoped HKCU")
    actual = {row[0] for row in file_rows}
    if len(actual) != len(file_rows) or actual != set(expected):
        raise ValueError("MSI File table differs from approved payload")
    if any(filename or directory not in allowed_directories or mode != "2" for filename, directory, mode in removals):
        raise ValueError("MSI removal may only remove empty installer directories on uninstall")


def validate_upgrade_rows(properties, rows, architecture, version):
    """Accept only the pinned WiX MajorUpgrade contract, never a broader product search."""
    if (architecture not in UPGRADE_CODES or not isinstance(version, str)
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            or any(int(v) > limit for v, limit in zip(version.split("."), (255, 255, 65535)))):
        raise ValueError("Unqualified MSI Upgrade architecture or version")
    family = "{" + UPGRADE_CODES[architecture] + "}"
    if properties.get("UpgradeCode") != family or properties.get("ProductLanguage") != "1033":
        raise ValueError("MSI Upgrade identity differs from the authored architecture family/language")
    if properties.get("ProductVersion") != version:
        raise ValueError("MSI version differs from bundle")
    # WiX v7.0.0 Compiler.cs: ParseMajorUpgradeElement; the binder maps its flags
    # directly (CreateWindowsInstallerDataFromIRCommand.AddUpgradeSymbol).
    # https://github.com/wixtoolset/wix/blob/v7.0.0/src/wix/WixToolset.Core/Compiler.cs
    # https://learn.microsoft.com/en-us/windows/win32/msi/upgrade-table
    # Database.query reads nullable fields as "". Flags: MigrateFeatures=1,
    # OnlyDetect=2; both version bounds exclusive, Remove=null means all features.
    expected = {
        (family, "", version, "1033", "1", "", "WIX_UPGRADE_DETECTED"),
        (family, version, "", "1033", "2", "", "WIX_DOWNGRADE_DETECTED"),
    }
    if len(rows) != 2 or {tuple(row) for row in rows} != expected:
        raise ValueError("MSI Upgrade rows differ from the authored MajorUpgrade contract")
    # WiX v7.0.0 CreateSpecialPropertiesCommand secures/sorts the action properties.
    secure = "WIX_DOWNGRADE_DETECTED;WIX_UPGRADE_DETECTED"
    if properties.get("SecureCustomProperties") != secure:
        raise ValueError("MSI Upgrade SecureCustomProperties differ from authoring")
    if any(name in properties for name in secure.split(";")):
        raise ValueError("MSI Upgrade detection properties must not be pre-populated")


class Database:
    def __init__(self, path):
        self.dll = ctypes.WinDLL("msi", use_last_error=True)
        from ctypes import wintypes as w
        signatures = {
            "MsiOpenDatabaseW": ([w.LPCWSTR, w.LPCWSTR, ctypes.POINTER(w.UINT)], w.UINT),
            "MsiDatabaseOpenViewW": ([w.UINT, w.LPCWSTR, ctypes.POINTER(w.UINT)], w.UINT),
            "MsiViewExecute": ([w.UINT, w.UINT], w.UINT),
            "MsiViewFetch": ([w.UINT, ctypes.POINTER(w.UINT)], w.UINT),
            "MsiRecordGetStringW": ([w.UINT, w.UINT, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.UINT),
            "MsiRecordReadStream": ([w.UINT, w.UINT, ctypes.c_void_p, ctypes.POINTER(w.DWORD)], w.UINT),
            "MsiGetSummaryInformationW": ([w.UINT, w.LPCWSTR, w.UINT, ctypes.POINTER(w.UINT)], w.UINT),
            "MsiSummaryInfoGetPropertyW": ([w.UINT, w.UINT, ctypes.POINTER(w.UINT), ctypes.POINTER(ctypes.c_int),
                                           ctypes.POINTER(w.FILETIME), w.LPWSTR, ctypes.POINTER(w.DWORD)], w.UINT),
            "MsiCloseHandle": ([w.UINT], w.UINT),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, result
        self.handle = w.UINT()
        self.require(self.dll.MsiOpenDatabaseW(str(Path(path).resolve()), None, ctypes.byref(self.handle)))

    @staticmethod
    def require(code):
        if code:
            raise OSError("MSI read failed: " + str(code))

    def query(self, sql, columns, stream=None):
        from ctypes import wintypes as w
        view = w.UINT()
        self.require(self.dll.MsiDatabaseOpenViewW(self.handle, sql, ctypes.byref(view)))
        rows = []
        try:
            self.require(self.dll.MsiViewExecute(view, 0))
            while True:
                record = w.UINT()
                code = self.dll.MsiViewFetch(view, ctypes.byref(record))
                if code == 259:
                    break
                self.require(code)
                try:
                    row = []
                    for index in range(1, columns + 1):
                        if stream == index:
                            chunks, total = [], 0
                            while True:
                                size = w.DWORD(65536)
                                buffer = ctypes.create_string_buffer(size.value)
                                self.require(self.dll.MsiRecordReadStream(record, index, buffer, ctypes.byref(size)))
                                if not size.value:
                                    break
                                total += size.value
                                if total > 250 * 1024 * 1024:
                                    raise ValueError("Oversized MSI cabinet")
                                chunks.append(buffer.raw[:size.value])
                            row.append(b"".join(chunks))
                        else:
                            size = w.DWORD(32768)
                            buffer = ctypes.create_unicode_buffer(size.value)
                            self.require(self.dll.MsiRecordGetStringW(record, index, buffer, ctypes.byref(size)))
                            row.append(buffer.value)
                    rows.append(row)
                    if len(rows) > 5000:
                        raise ValueError("Oversized MSI table")
                finally:
                    self.dll.MsiCloseHandle(record)
        finally:
            self.dll.MsiCloseHandle(view)
        return rows

    def summary_string(self, property_id):
        from ctypes import wintypes as w
        summary = w.UINT()
        self.require(self.dll.MsiGetSummaryInformationW(self.handle, None, 0, ctypes.byref(summary)))
        try:
            kind, size = w.UINT(), w.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            self.require(self.dll.MsiSummaryInfoGetPropertyW(summary, property_id, ctypes.byref(kind),
                                                           None, None, buffer, ctypes.byref(size)))
            if kind.value != 30:  # VT_LPSTR: the MSI SummaryInformation Template is a string.
                raise ValueError("MSI summary property is not a string")
            return buffer.value
        finally:
            self.dll.MsiCloseHandle(summary)

    def close(self):
        self.dll.MsiCloseHandle(self.handle)


def audit(msi, archive):
    if os.name != "nt":
        raise ValueError("MSI content auditing requires Windows")
    import zipfile
    manifest = scan_bundle(archive)
    with zipfile.ZipFile(archive) as zipped:
        contents = {n.removeprefix("Kshetrajna/"): zipped.read(n) for n in zipped.namelist() if not n.endswith("/")}
    expected = {identifier("file:" + name): (name, content) for name, content in contents.items()}
    database = Database(msi)
    try:
        tables = {r[0] for r in database.query("SELECT `Name` FROM `_Tables`", 1)}
        properties = dict(database.query("SELECT `Property`, `Value` FROM `Property`", 2))
        registry = database.query("SELECT `Root`, `Key` FROM `Registry`", 2)
        file_rows = database.query("SELECT `File` FROM `File`", 1)
        allowed_directories = {"ProgramsFolder", "VendorFolder", "INSTALLFOLDER"}
        for name in contents:
            parts = name.split("/")[:-1]
            allowed_directories.update(identifier("dir:" + "/".join(parts[:i])) for i in range(1, len(parts) + 1))
        removals = database.query("SELECT `FileName`, `DirProperty`, `InstallMode` FROM `RemoveFile`", 3) if "RemoveFile" in tables else []
        validate_tables(tables, properties, registry, file_rows, expected, removals, allowed_directories)
        upgrades = database.query("SELECT `UpgradeCode`, `VersionMin`, `VersionMax`, `Language`, `Attributes`, `Remove`, `ActionProperty` FROM `Upgrade`", 7) if "Upgrade" in tables else []
        validate_upgrade_rows(properties, upgrades, manifest.get("architecture"), manifest.get("version"))
        if database.summary_string(7).casefold() != manifest["architecture"] + ";1033":
            raise ValueError("MSI Upgrade platform/language metadata differs from bundle authoring")
        cabinets = database.query("SELECT `Cabinet` FROM `Media`", 1)
        if len(cabinets) != 1 or not cabinets[0][0].startswith("#"):
            raise ValueError("Expected one embedded cabinet; no external payloads")
        cabinet_name = cabinets[0][0][1:]
        if not cabinet_name.replace("_", "").replace(".", "").isalnum():
            raise ValueError("Unsafe MSI cabinet name")
        rows = database.query("SELECT `Data` FROM `_Streams` WHERE `Name`='" + cabinet_name + "'", 1, stream=1)
        if len(rows) != 1:
            raise ValueError("Missing MSI cabinet")
    finally:
        database.close()
    with tempfile.TemporaryDirectory(prefix="kshetrajna-msi-audit-") as name:
        root = Path(name)
        cabinet = root / "payload.cab"
        cabinet.write_bytes(rows[0][0])
        destination = root / "expanded"
        destination.mkdir()
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/expand.exe"), "-F:*", str(cabinet), str(destination)], check=True, capture_output=True)
        paths = list(destination.iterdir())
        if {p.name for p in paths} != set(expected) or any(not p.is_file() or p.is_symlink() for p in paths):
            raise ValueError("MSI cabinet has unlisted or missing payload")
        for path in paths:
            original, content = expected[path.name]
            observed = path.read_bytes()
            scan_content(original, observed)
            if observed != content:
                raise ValueError("MSI cabinet byte mismatch: " + original)
    return {"status": "passed", "msi_sha256": digest(msi), "payload_sha256": digest(archive)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--msi", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    check_python()
    print(json.dumps(audit(args.msi, args.archive)))


if __name__ == "__main__":
    main()
