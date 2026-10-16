"""Bind native reports, verified signature, real MSI contents and lifecycle to exact bytes."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from audit_msi import audit
from release_gate import candidate, validate_native
from release_tools import ROOT, check_python, digest, inventory as generate_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified", type=Path, required=True)
    parser.add_argument("--lifecycle", type=Path, required=True)
    parser.add_argument("--msi", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args()
    check_python()
    identity = candidate()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Final evidence requires the qualifying CI run")
    audit_result = audit(args.msi, args.archive)
    subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(ROOT / "packaging/windows/verify-authenticode.ps1"),
                    "-Path", str(args.msi.resolve()), "-Thumbprint", os.environ["SIGNING_THUMBPRINT"],
                    "-SignTool", os.environ["SIGNTOOL_PATH"], "-SignToolSha256", os.environ["SIGNTOOL_SHA256"]], check=True)
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    lifecycle = read(args.lifecycle)
    architecture = lifecycle["architecture"]
    if lifecycle["msi_sha256"] != audit_result["msi_sha256"] or lifecycle["source_commit"] != identity["source_commit"]:
        raise ValueError("Lifecycle evidence belongs to another artifact/revision")
    evidence = {runtime: read(args.qualified / (runtime + ".json")) for runtime in ("source", "packaged")}
    for runtime, report in evidence.items():
        validate_native(report, identity["source_commit"], architecture, runtime)
    reproducible = read(args.qualified / "reproducible.json")
    if (reproducible.get("status") != "passed" or reproducible.get("kind") != "windows"
            or reproducible.get("architecture") != architecture or reproducible.get("sha256") != digest(args.archive)):
        raise ValueError("No reproducibility result for the exact unsigned payload")
    if reproducible.get("source_commit") != identity["source_commit"] or reproducible.get("dirty_source") is not False:
        raise ValueError("Unsigned reproducibility evidence must come from the exact clean revision")
    evidence.update(lifecycle=lifecycle, authenticode="verified", reproducible_unsigned=True,
                    content_scan=audit_result["status"], msi_sha256=digest(args.msi), unsigned_payload_sha256=digest(args.archive))
    # Qualification directories can contain a swapped/stale inventory. Rebuild
    # every file hash and runtime identity from the exact archive audited above.
    generate_inventory(args.archive, args.inventory)
    if digest(args.archive) != audit_result["payload_sha256"] or digest(args.msi) != audit_result["msi_sha256"]:
        raise ValueError("Artifacts changed after the MSI content audit")
    inventory = read(args.inventory)
    # Bind the known-component inventory to the signed MSI, retaining its honest scope.
    inventory["documentNamespace"] = "https://spdx.org/spdxdocs/kshetrajna-msi-" + digest(args.msi)
    inventory["name"] = args.msi.name + " known-component inventory"
    inventory["packages"].append({"SPDXID": "SPDXRef-Installer", "name": args.msi.name,
                                   "versionInfo": identity["version"], "downloadLocation": "NOASSERTION",
                                   "checksums": [{"algorithm": "SHA256", "checksumValue": digest(args.msi)}],
                                   "filesAnalyzed": False, "licenseConcluded": "NOASSERTION", "licenseDeclared": "NOASSERTION",
                                   "copyrightText": "NOASSERTION"})
    inventory["relationships"].extend([
        {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-Installer"},
        {"spdxElementId": "SPDXRef-Installer", "relationshipType": "CONTAINS", "relatedSpdxElement": "SPDXRef-App"}])
    args.inventory.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
