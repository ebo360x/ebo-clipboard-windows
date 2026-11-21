"""يتأكد إن الـ exe اللي طلع من PyInstaller فيه الموديولات المهمة.

الاستخدام: python tools/check_bundle.py "build/release/ebo clipboard.exe" 1.4
لو حاجة ناقصة (زي keyring) بيطلع بكود خطأ علشان السكريبت يوقف قبل الرفع.
"""
import os
import sys
import tempfile

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

REQUIRED = ("keyring", "keyring.backends.Windows", "sync", "db", "ui", "hotkeys", "version", "updater", "ditto_import", "truststore", "PyQt6")
REQUIRED_DATA = ("icon.png",)


def main(exe_path, expected_version=None):
    archive = CArchiveReader(exe_path)
    names = list(archive.toc)
    pyz_name = next((n for n in names if n.endswith(".pyz")), None)
    if pyz_name is None:
        print("FAIL: no PYZ archive inside the exe")
        return 1

    fd, pyz_path = tempfile.mkstemp(suffix=".pyz")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(archive.extract(pyz_name))
        modules = set(ZlibArchiveReader(pyz_path).toc)
    finally:
        os.remove(pyz_path)

    missing = [m for m in REQUIRED if m not in modules]
    missing += [d for d in REQUIRED_DATA if d not in names]
    if not any(n.startswith("fonts") for n in names):
        missing.append("fonts/")
    if missing:
        print("FAIL: missing from bundle: " + ", ".join(missing))
        return 1

    if expected_version:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        namespace = {}
        with open(os.path.join(here, "version.py"), encoding="utf-8") as handle:
            exec(handle.read(), namespace)
        if namespace.get("__version__") != expected_version:
            print(f"FAIL: version.py says {namespace.get('__version__')}, expected {expected_version}")
            return 1

    print(f"OK: bundle has {len(modules)} modules, all required ones present")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
