"""Author/build a per-user MSI with the hash-pinned, project-local WiX CLI."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from release_tools import ROOT, check_python, digest, safe_name, scan_bundle

NS = "http://wixtoolset.org/schemas/v4/wxs"
ET.register_namespace("", NS)
UPGRADE_CODES = {"x64": "6684D92A-3081-51F4-BDB2-61A0D842E6BE", "arm64": "D184D1ED-5295-5147-974C-D1DF3B040993"}


def element(parent, tag, **attributes):
    return ET.SubElement(parent, "{" + NS + "}" + tag, attributes)


def identifier(name):
    return "id" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]


def author_payload(bundle, output):
    bundle = Path(bundle).resolve()
    manifest = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
    if manifest.get("dirty_source") is not False or manifest.get("architecture") not in UPGRADE_CODES:
        raise ValueError("MSI requires a clean, identified bundle")
    expected = set(manifest["files"]) | {"bundle.json"}
    actual = set()
    for path in bundle.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("MSI payload must not contain links")
        if path.is_file():
            name = path.relative_to(bundle).as_posix()
            safe_name(name)
            actual.add(name)
    if actual != expected:
        raise ValueError("MSI payload has missing or unlisted files")
    for name, checksum in manifest["files"].items():
        if digest(bundle / name) != checksum:
            raise ValueError("MSI payload checksum mismatch")
    root = ET.Element("{" + NS + "}Wix")
    fragment = element(root, "Fragment")
    directory = element(fragment, "DirectoryRef", Id="INSTALLFOLDER")
    directories = {"": directory}
    group = element(fragment, "ComponentGroup", Id="Payload")
    architecture = manifest["architecture"]
    namespace = uuid.UUID(UPGRADE_CODES[architecture])
    for name in sorted(actual):
        parts = name.split("/")
        parent = ""
        for part in parts[:-1]:
            key = parent + "/" + part if parent else part
            if key not in directories:
                directories[key] = element(directories[parent], "Directory", Id=identifier("dir:" + key), Name=part)
            parent = key
        component_id = identifier("component:" + name)
        component = element(directories[parent], "Component", Id=component_id,
                            Guid=str(uuid.uuid5(namespace, name)).upper(), Bitness="always64")
        element(component, "File", Id=identifier("file:" + name), Name=parts[-1],
                Source="$(var.PayloadRoot)\\" + name.replace("/", "\\"))
        # HKCU key paths make repair and removal user-scoped (ICE38).
        element(component, "RegistryValue", Root="HKCU", Key="Software\\Kshetrajna\\MSI\\" + architecture,
                Name=component_id, Type="integer", Value="1", KeyPath="yes")
        element(group, "ComponentRef", Id=component_id)
    # ICE64: remove only empty installer-owned directories, never recurse or touch history.
    cleanup = element(directory, "Component", Id="DirectoryCleanup", Guid=str(uuid.uuid5(namespace, "directories")).upper(), Bitness="always64")
    element(cleanup, "RegistryValue", Root="HKCU", Key="Software\\Kshetrajna\\MSI\\" + architecture,
            Name="DirectoryCleanup", Type="integer", Value="1", KeyPath="yes")
    for key in ("ProgramsFolder", "VendorFolder", "INSTALLFOLDER", *[identifier("dir:" + n) for n in directories if n]):
        element(cleanup, "RemoveFolder", Id=identifier("remove:" + key), Directory=key, On="uninstall")
    element(group, "ComponentRef", Id="DirectoryCleanup")
    ET.indent(root)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return manifest


def provision_wix(destination):
    lock = json.loads((ROOT / "packaging/windows/toolchain.lock.json").read_text(encoding="utf-8"))["wix"]
    unpack_tool(lock, destination, "sha256")
    return Path(destination).resolve() / lock["entrypoint"]


def unpack_tool(lock, destination, algorithm):
    destination = Path(destination).resolve()
    # Re-extract verified archive each time; never trust a previously modified executable.
    destination.mkdir(parents=True, exist_ok=False)
    request = urllib.request.Request(lock["url"], headers={"User-Agent": "Kshetrajna-release/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(80 * 1024 * 1024 + 1)
    if len(data) > 80 * 1024 * 1024 or hashlib.new(algorithm, data).hexdigest() != lock[algorithm]:
        raise ValueError("Toolchain checksum mismatch")
    archive = destination / "download.zip"
    archive.write_bytes(data)
    with zipfile.ZipFile(archive) as zipped:
        if len(zipped.infolist()) > 5000 or sum(m.file_size for m in zipped.infolist()) > 400 * 1024 * 1024:
            raise ValueError("Oversized toolchain archive")
        seen = set()
        for member in zipped.infolist():
            safe_name(member.filename.rstrip("/"))
            if member.filename.casefold() in seen or (member.external_attr >> 16 & 0o170000) not in (0, 0o100000, 0o040000):
                raise ValueError("Unsafe WiX package")
            seen.add(member.filename.casefold())
        zipped.extractall(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True, help="Must not exist")
    args = parser.parse_args()
    check_python()
    if os.name != "nt":
        raise ValueError("MSI compilation requires Windows")
    if os.environ.get("KSHETRAJNA_WIX_EULA_ACCEPTED") != "yes":
        raise ValueError("Review WiX's upstream OSMF EULA; set KSHETRAJNA_WIX_EULA_ACCEPTED=yes only if applicable terms are met")
    manifest = scan_bundle(args.archive)
    if manifest["dirty_source"]:
        raise ValueError("Refusing MSI from dirty source")
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(args.archive) as zipped:
        zipped.extractall(work / "payload")
    bundle = work / "payload/Kshetrajna"
    source = work / "Payload.wxs"
    manifest = author_payload(bundle, source)
    version = manifest["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or any(int(v) > limit for v, limit in zip(version.split("."), (255, 255, 65535))):
        raise ValueError("Version does not fit Windows Installer")
    wix = provision_wix(work / "wix")
    runtime = json.loads((ROOT / "packaging/windows/toolchain.lock.json").read_text(encoding="utf-8"))["dotnet"]
    unpack_tool(runtime[manifest["architecture"]], work / "dotnet", "sha512")
    dotnet = work / "dotnet/dotnet.exe"
    environment = {**os.environ, "DOTNET_ROOT": str(dotnet.parent), "DOTNET_MULTILEVEL_LOOKUP": "0",
                   "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    actual = subprocess.check_output([str(dotnet), str(wix), "--version"], text=True, env=environment).strip()
    if actual.split("+", 1)[0] != "7.0.0":
        raise ValueError("Wrong WiX tool version: " + actual)
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(dotnet), str(wix), "build", str(ROOT / "packaging/windows/Package.wxs"), str(source),
                    "-arch", manifest["architecture"], "-d", "Version=" + version,
                    "-d", "Architecture=" + manifest["architecture"], "-d", "UpgradeCode=" + UPGRADE_CODES[manifest["architecture"]],
                    "-d", "PayloadRoot=" + str(bundle), "-intermediatefolder", str(work / "obj"),
                    "-pdbtype", "none", "-o", str(args.output.resolve())], check=True, env=environment)
    print("Unsigned MSI built; Authenticode signing and disposable native lifecycle qualification are required: " + str(args.output))


if __name__ == "__main__":
    main()
