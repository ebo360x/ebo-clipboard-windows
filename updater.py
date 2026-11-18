"""التحديث التلقائي من GitHub Releases.

- بنعرف آخر نسخة من التحويلة (redirect) بتاعة /releases/latest بدل GitHub API،
  علشان الـ API ليه حد 60 طلب في الساعة لكل IP، وكل الشركة طالعة من IP واحد.
- التحديث بيشتغل في نسخة الـ exe بس (مش وانت شغال من الكود).
- التبديل: ويندوز بيسمح بتغيير اسم exe شغال، فبنسمّي القديم .old ونحط الجديد
  مكانه، ونشغّل الجديد بـ --after-update <pid> فيستنى القديم يقفل الأول.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

from version import __version__

REPO = "ebo360x/ebo-clipboard-windows"
ASSET_NAME = "ebo-clipboard.zip"
EXE_NAME = "ebo clipboard.exe"
# متغير البيئة ده للاختبار بس (سيرفر محلي بدل GitHub)
BASE_URL = os.environ.get("EBO_UPDATE_BASE", f"https://github.com/{REPO}").rstrip("/")
USER_AGENT = "ebo-clipboard-updater"
AFTER_UPDATE_FLAG = "--after-update"


class UpdateError(Exception):
    pass


def log(message):
    """سجل بسيط جنب الـ exe (update.log) علشان لو التحديث فشل عند حد نعرف ليه."""
    if not getattr(sys, "frozen", False):
        return
    try:
        path = os.path.join(os.path.dirname(current_exe()), "update.log")
        if os.path.exists(path) and os.path.getsize(path) > 200_000:
            os.remove(path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + f"[{__version__} pid {os.getpid()}] {message}" + os.linesep)
    except OSError:
        pass


def parse_version(text):
    parts = []
    for piece in str(text).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote, local=__version__):
    return parse_version(remote) > parse_version(local)


def current_exe():
    return os.path.abspath(sys.executable)


def can_self_update():
    """True لو شغالين من exe والفولدر بتاعه نقدر نكتب فيه."""
    if not getattr(sys, "frozen", False):
        return False
    return os.access(os.path.dirname(current_exe()), os.W_OK)


# ---------------------------------------------------------------- الشبكة
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def latest_version(timeout=15):
    """رقم آخر release (اسم التاج)، من غير ما نستهلك حد GitHub API."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(f"{BASE_URL}/releases/latest", method="HEAD",
                                     headers={"User-Agent": USER_AGENT})
    try:
        response = opener.open(request, timeout=timeout)
        location = response.headers.get("Location")
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location") if error.code in (301, 302, 303, 307, 308) else None
        if location is None:
            raise UpdateError(f"GitHub {error.code}") from error
    except (urllib.error.URLError, OSError) as error:
        raise UpdateError(f"تعذر الاتصال: {getattr(error, 'reason', error)}") from error
    if not location or "/tag/" not in location:
        raise UpdateError("مفيش نسخ منشورة")
    return urllib.request.unquote(location.rstrip("/").rsplit("/tag/", 1)[1])


def download_update(tag, progress=None, timeout=30, attempts=3):
    """ينزّل الـ zip ويطلّع منه الـ exe جنب الـ exe الحالي باسم .new، ويرجّع مساره."""
    url = f"{BASE_URL}/releases/download/{urllib.request.quote(tag)}/{ASSET_NAME}"
    fd, zip_path = tempfile.mkstemp(prefix="ebo-update-", suffix=".zip")
    os.close(fd)
    try:
        last_error = None
        for attempt in range(attempts):
            if attempt:
                time.sleep(2 * attempt)
            try:
                _download(url, zip_path, progress, timeout)
                break
            except (urllib.error.URLError, OSError) as error:
                last_error = error
        else:
            raise UpdateError(f"فشل التنزيل: {getattr(last_error, 'reason', last_error)}")

        target = current_exe() + ".new"
        try:
            with zipfile.ZipFile(zip_path) as archive:
                member = next((n for n in archive.namelist() if os.path.basename(n) == EXE_NAME), None)
                if member is None:
                    raise UpdateError("الملف اللي اتنزل مافيهوش البرنامج")
                with archive.open(member) as source, open(target, "wb") as dest:
                    while True:
                        chunk = source.read(1 << 20)
                        if not chunk:
                            break
                        dest.write(chunk)
        except zipfile.BadZipFile as error:
            raise UpdateError("الملف اللي اتنزل بايظ") from error

        with open(target, "rb") as handle:
            header = handle.read(2)
        if header != b"MZ" or os.path.getsize(target) < 1_000_000:
            os.remove(target)
            raise UpdateError("الملف اللي اتنزل مش برنامج سليم")
        return target
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass


def _download(url, path, progress, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(path, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
        if total and done != total:
            raise OSError("التنزيل اتقطع في النص")


# ---------------------------------------------------------------- التبديل
def install_and_restart(new_exe):
    """يبدّل الـ exe ويشغّل الجديد. بعدها لازم البرنامج الحالي يقفل."""
    exe = current_exe()
    old = exe + ".old"
    if os.path.exists(old):
        try:
            os.remove(old)
        except OSError:
            old = f"{exe}.{int(time.time())}.old"
    log(f"installing update: {new_exe}")
    os.replace(exe, old)
    try:
        os.replace(new_exe, exe)
        _spawn_detached([exe, AFTER_UPDATE_FLAG, str(os.getpid())], os.path.dirname(exe))
    except Exception:
        # رجّع كل حاجة زي ما كانت
        if os.path.exists(exe):
            os.replace(exe, new_exe)
        os.replace(old, exe)
        raise


def _spawn_detached(args, cwd):
    """يشغّل النسخة الجديدة بحيث ماتقفلش لما القديمة تقفل.

    الـ exe اللي معمول بـ PyInstaller شغال جوه Windows Job بيقفل كل البرامج
    اللي فتحها لما يقفل، فلازم النسخة الجديدة تطلع برّه الـ Job ده.
    """
    base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    # من غير ده النسخة الجديدة بتورث متغيرات PyInstaller بتاعة القديمة وبتحاول
    # تستخدم الفولدر المؤقت بتاعها اللي بيتمسح لما القديمة تقفل، فتقع.
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    try:
        child = subprocess.Popen(args, cwd=cwd, env=env, creationflags=base | breakaway, close_fds=True)
        log(f"started new version pid {child.pid} (outside job)")
    except OSError as error:
        # الـ Job مش سامح بالخروج منه؛ نجرب من غيره
        log(f"breakaway not allowed ({error}); starting normally")
        child = subprocess.Popen(args, cwd=cwd, env=env, creationflags=base, close_fds=True)
        log(f"started new version pid {child.pid}")


def wait_for_previous_instance(argv, timeout_ms=30000):
    """في النسخة الجديدة: استنى النسخة القديمة تقفل قبل ما نكمّل."""
    if AFTER_UPDATE_FLAG not in argv:
        return False
    try:
        pid = int(argv[argv.index(AFTER_UPDATE_FLAG) + 1])
    except (IndexError, ValueError):
        return True
    log(f"started after update, waiting for old pid {pid}")
    if sys.platform == "win32":
        import ctypes
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, timeout_ms)
            ctypes.windll.kernel32.CloseHandle(handle)
            log(f"old version closed (wait result {result})")
        else:
            log("old version already closed")
    return True


def cleanup_old_versions():
    """يمسح الـ exe القديم اللي فضل بعد التحديث."""
    if not getattr(sys, "frozen", False):
        return
    folder = os.path.dirname(current_exe())
    for name in os.listdir(folder):
        if name.startswith(EXE_NAME) and (name.endswith(".old") or name.endswith(".new")):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


# ---------------------------------------------------------------- المدير (Qt)
try:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
except Exception:  # pragma: no cover
    QObject = object
    QTimer = None


if QTimer is not None:
    class UpdateManager(QObject):
        update_available = pyqtSignal(str)          # tag
        check_finished = pyqtSignal(str, str)       # tag أو "", رسالة خطأ أو ""
        download_progress = pyqtSignal(int, int)    # done, total
        download_finished = pyqtSignal(str, str)    # مسار الـ exe الجديد أو "", رسالة خطأ أو ""

        CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000

        def __init__(self, parent=None):
            super().__init__(parent)
            self.latest = None
            self.dismissed = set()
            self._checking = False
            self._manual = False
            self.check_finished.connect(self._on_checked)
            self._timer = QTimer(self)
            self._timer.setInterval(self.CHECK_INTERVAL_MS)
            self._timer.timeout.connect(lambda: self.check(manual=False))

        def start(self, first_delay_ms=15000):
            if not getattr(sys, "frozen", False):
                return  # وانت شغال من الكود مفيش تحديث تلقائي
            cleanup_old_versions()
            QTimer.singleShot(first_delay_ms, lambda: self.check(manual=False))
            self._timer.start()

        def check(self, manual=True):
            if self._checking:
                return
            self._checking = True

            def work():
                try:
                    tag = latest_version()
                    self.check_finished.emit(tag, "")
                except Exception as error:  # noqa: BLE001
                    self.check_finished.emit("", str(error))

            self._manual = manual
            threading.Thread(target=work, daemon=True).start()

        def _on_checked(self, tag, error):
            self._checking = False
            if error or not tag:
                return
            self.latest = tag
            if is_newer(tag) and (self._manual or tag not in self.dismissed):
                self.update_available.emit(tag)

        def dismiss(self, tag):
            self.dismissed.add(tag)

        def download(self, tag):
            def work():
                try:
                    path = download_update(tag, progress=lambda d, t: self.download_progress.emit(d, t))
                    self.download_finished.emit(path, "")
                except Exception as error:  # noqa: BLE001
                    self.download_finished.emit("", str(error))

            threading.Thread(target=work, daemon=True).start()
