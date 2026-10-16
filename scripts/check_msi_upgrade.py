"""Read-only MSI upgrade-family and current-user product-registration checks."""

import argparse
import ctypes
import json
import re
from pathlib import Path

from audit_msi import Database
from build_msi import UPGRADE_CODES
from release_tools import check_python


def guid(value):
    if not isinstance(value, str) or not re.fullmatch(r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}", value):
        raise ValueError("MSI requires a valid ProductCode and UpgradeCode")
    return value.upper()


def read_identity(path):
    database = Database(path)
    try:
        properties = dict(database.query("SELECT `Property`, `Value` FROM `Property`", 2))
        # SummaryInformation PID_TEMPLATE is MSI platform metadata, not a filename/label.
        platform = database.summary_string(7).split(";", 1)[0].casefold()
        return {"product_code": guid(properties.get("ProductCode")), "upgrade_code": guid(properties.get("UpgradeCode")),
                "architecture": {"x64": "x64", "arm64": "arm64"}.get(platform),
                "version": properties.get("ProductVersion"),
                "per_user": not properties.get("ALLUSERS", "") and properties.get("MSIINSTALLPERUSER", "1") == "1"}
    finally:
        database.close()


def validate_upgrade(predecessor, candidate, architecture):
    expected_family = "{" + UPGRADE_CODES[architecture] + "}"
    for identity in (predecessor, candidate):
        if identity.get("architecture") != architecture or identity.get("per_user") is not True:
            raise ValueError("Both MSIs must be per-user packages for the expected native architecture")
        if guid(identity.get("upgrade_code")) != expected_family:
            raise ValueError("Both MSIs must belong to the expected Kshetrajna UpgradeCode family")
        guid(identity.get("product_code"))
        if not isinstance(identity.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", identity["version"]):
            raise ValueError("MSI ProductVersion must have three numeric parts")
    if guid(predecessor["product_code"]) == guid(candidate["product_code"]):
        raise ValueError("A major upgrade must have a different ProductCode")
    if tuple(map(int, predecessor["version"].split("."))) >= tuple(map(int, candidate["version"].split("."))):
        raise ValueError("The signed predecessor must have a strictly lower ProductVersion")


def validate_registration(predecessor_state, candidate_state, phase):
    # MsiQueryProductState: -1=UNKNOWN, 5=DEFAULT (installed for current user).
    # Advertised/absent/broken/error states never prove either required outcome.
    expected = {"clean": (-1, -1), "predecessor": (5, -1), "upgraded": (-1, 5), "uninstalled": (-1, -1)}[phase]
    for label, actual, required in zip(("predecessor", "candidate"), (predecessor_state, candidate_state), expected):
        if type(actual) is not int or actual != required:
            outcome = "unregistered" if required == -1 else "installed for the current user"
            raise ValueError(f"{phase}: {label} ProductCode must be {outcome}; Windows Installer state={actual}")


def product_state(product_code):
    query = ctypes.WinDLL("msi", use_last_error=True).MsiQueryProductStateW
    query.argtypes, query.restype = [ctypes.c_wchar_p], ctypes.c_int
    return query(product_code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predecessor", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--architecture", choices=UPGRADE_CODES, required=True)
    parser.add_argument("--phase", choices=("clean", "predecessor", "upgraded", "uninstalled"), required=True)
    args = parser.parse_args()
    check_python()
    predecessor, candidate = read_identity(args.predecessor), read_identity(args.candidate)
    validate_upgrade(predecessor, candidate, args.architecture)
    validate_registration(product_state(predecessor["product_code"]), product_state(candidate["product_code"]), args.phase)
    print(json.dumps({"predecessor": predecessor, "candidate": candidate, "phase": args.phase}))


if __name__ == "__main__":
    main()
