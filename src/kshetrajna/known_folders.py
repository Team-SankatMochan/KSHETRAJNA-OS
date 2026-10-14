"""Discovery of conventional user folders without scanning their contents."""

import os
import ctypes
import uuid
from pathlib import Path


KNOWN_FOLDER_NAMES = ("Desktop", "Downloads", "Documents", "Pictures", "Music", "Videos", "OneDrive")


def _windows_known_folders() -> dict[str, str]:
    """Read Windows' actual redirected locations without enumerating contents."""
    if os.name != 'nt':
        return {}
    identifiers = {
        'Desktop': 'B4BFCC3A-DB2C-424C-B029-7FE99A87C641',
        'Downloads': '374DE290-123F-4565-9164-39C4925E467B',
        'Documents': 'FDD39AD0-238F-46AF-ADB4-6C85480369C7',
        'Pictures': '33E28130-4E1E-4676-835A-98395C3BC3BB',
        'Music': '4BD8D571-6D19-48D3-BE97-422220080E43',
        'Videos': '18989B1D-99B5-455B-841C-AB7C74E4DDFC',
    }
    result = {}
    try:
        shell = ctypes.WinDLL('shell32')
        ole = ctypes.WinDLL('ole32')
        lookup = shell.SHGetKnownFolderPath
        lookup.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_void_p)]
        lookup.restype = ctypes.c_long
        release = ole.CoTaskMemFree
        release.argtypes = [ctypes.c_void_p]
        release.restype = None
        for name, identifier in identifiers.items():
            guid = ctypes.create_string_buffer(uuid.UUID(identifier).bytes_le, 16)
            pointer = ctypes.c_void_p()
            try:
                if lookup(guid, 0, None, ctypes.byref(pointer)) == 0 and pointer.value:
                    result[name] = ctypes.wstring_at(pointer.value)
            finally:
                if pointer.value:
                    release(pointer)
    except (OSError, AttributeError):
        pass
    return result


def discover_known_folders() -> list[dict]:
    """Return existing conventional folders and their paths.

    This only checks whether the directory itself exists. It never enumerates
    files. The caller must still ask the user to approve a folder before the
    file scanner starts.
    """
    profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    native = _windows_known_folders()
    candidates = []
    seen = set()
    def add(identifier, label, path):
        try:
            path = Path(path)
            key = os.path.normcase(str(path.resolve()))
            if path.is_dir() and key not in seen:
                seen.add(key)
                candidates.append({'id': identifier, 'label': label, 'path': str(path)})
        except OSError:
            pass
    for name in KNOWN_FOLDER_NAMES:
        add(name.casefold(), name, native.get(name, profile / name))

    # OneDrive commonly redirects the known folders into a tenant-specific
    # directory. Add only the conventional child folders and de-duplicate by
    # resolved path; this still performs no content scan.
    cloud_roots = [profile / 'OneDrive', profile / 'OneDrive - Personal']
    cloud_roots.extend(Path(os.environ[key]) for key in
                       ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial') if os.environ.get(key))
    for one_drive in cloud_roots:
        if not one_drive.is_dir():
            continue
        for name in ("Desktop", "Downloads", "Documents"):
            path = one_drive / name
            add(f'onedrive-{name.casefold()}', f'OneDrive {name}', path)
    return candidates
