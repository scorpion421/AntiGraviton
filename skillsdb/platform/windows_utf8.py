"""
Windows native UTF-8 application manifest deployment and registry configuration.
"""

import os
import sys
from pathlib import Path

UTF8_MANIFEST_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <application>
    <windowsSettings>
      <activeCodePage xmlns="http://schemas.microsoft.com/SMI/2019/WindowsSettings">UTF-8</activeCodePage>
    </windowsSettings>
  </application>
</assembly>
"""


def get_antigravity_paths() -> Path:
    """Detect Antigravity installation root directory on Windows."""
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity",
        Path(os.environ.get("PROGRAMFILES", "")) / "antigravity",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "antigravity",
    ]
    for c in candidates:
        if c.exists() and (c / "Antigravity.exe").exists():
            return c
    return None


def check_windows_utf8() -> tuple[bool, str]:
    """Check whether Antigravity UTF-8 application manifests and environment are active."""
    if sys.platform != "win32":
        return True, "Non-Windows platform (native UTF-8)"

    ag_dir = get_antigravity_paths()
    if not ag_dir:
        return False, "Antigravity installation not detected in standard paths"

    exe_manifest = ag_dir / "Antigravity.exe.manifest"
    ls_manifest = ag_dir / "resources" / "bin" / "language_server.exe.manifest"

    has_exe_m = exe_manifest.exists() and "activeCodePage" in exe_manifest.read_text(encoding="utf-8", errors="ignore")
    has_ls_m = ls_manifest.exists() and "activeCodePage" in ls_manifest.read_text(encoding="utf-8", errors="ignore")

    if has_exe_m and has_ls_m:
        return True, "Application manifests active (<activeCodePage>UTF-8</activeCodePage>)"

    missing = []
    if not has_exe_m:
        missing.append("Antigravity.exe.manifest")
    if not has_ls_m:
        missing.append("language_server.exe.manifest")
    return False, f"Missing UTF-8 manifest for: {', '.join(missing)}"


def fix_utf8(check_only: bool = False):
    """
    Configures Antigravity to run natively with UTF-8 process code page on Windows.
    Deploys external .manifest files for Antigravity.exe and language_server.exe,
    and sets standard UTF-8 environment variables.
    """
    print("\n================ SKILLSDB UTF-8 ENCODING FIX ================")
    if sys.platform != "win32":
        print("[INFO] Operating system is not Windows. UTF-8 is already the native platform default.")
        print("=============================================================\n")
        return

    ag_dir = get_antigravity_paths()
    if not ag_dir:
        print("[FAIL] Could not locate Antigravity installation in standard directories:")
        print("  * %LOCALAPPDATA%\\Programs\\antigravity")
        print("  * %ProgramFiles%\\antigravity")
        print("=============================================================\n")
        return

    exe_manifest = ag_dir / "Antigravity.exe.manifest"
    ls_manifest = ag_dir / "resources" / "bin" / "language_server.exe.manifest"

    if check_only:
        ok, msg = check_windows_utf8()
        if ok:
            print(f"[OK] Antigravity UTF-8 Status: {msg}")
        else:
            print(f"[WARN] Antigravity UTF-8 Status: {msg}")
            print("Run 'skillsdb fix-utf8' to automatically install the required manifests.")
        print("=============================================================\n")
        return

    # 1. Write Antigravity.exe.manifest
    try:
        exe_manifest.write_text(UTF8_MANIFEST_XML.strip() + "\n", encoding="utf-8")
        print(f"[OK] Application manifest deployed: {exe_manifest}")
    except Exception as e:
        print(f"[FAIL] Could not write {exe_manifest}: {e}")

    # 2. Write language_server.exe.manifest
    try:
        ls_manifest.parent.mkdir(parents=True, exist_ok=True)
        ls_manifest.write_text(UTF8_MANIFEST_XML.strip() + "\n", encoding="utf-8")
        print(f"[OK] Application manifest deployed: {ls_manifest}")
    except Exception as e:
        print(f"[FAIL] Could not write {ls_manifest}: {e}")

    # 3. Configure User Environment Variables (HKCU\\Environment)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "PYTHONUTF8", 0, winreg.REG_SZ, "1")
            winreg.SetValueEx(key, "PYTHONIOENCODING", 0, winreg.REG_SZ, "utf-8")
            winreg.SetValueEx(key, "LANG", 0, winreg.REG_SZ, "de_DE.UTF-8")
            winreg.SetValueEx(key, "LC_ALL", 0, winreg.REG_SZ, "de_DE.UTF-8")
        print("[OK] User environment variables configured (PYTHONUTF8=1, PYTHONIOENCODING=utf-8, LANG=de_DE.UTF-8)")
    except Exception as e:
        print(f"[WARN] Could not update environment variables in registry: {e}")

    print("\n[SUCCESS] Antigravity UTF-8 configuration deployed successfully!")
    print("Restart Antigravity to activate native UTF-8 process code page.")
    print("=============================================================\n")
