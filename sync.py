"""مزامنة ClipMaster عبر GitHub (مستودع خاص) من غير سيرفر.

الفكرة: كل رسالة ومجموعة ليها معرّف ثابت ووقت آخر تعديل. عند المزامنة بنسحب
data.json من المستودع، وندمجه مع البيانات المحلية (آخر تعديل بيكسب)، وبعدين
نرفع النتيجة في commit واحد. لو جهاز تاني رفع في نفس الوقت بنعيد الدمج.
"""
import base64
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple

from db import SYNCED_SETTINGS, image_digest, now_ms

API_URL = "https://api.github.com"
DATA_PATH = "data.json"
IMAGES_DIR = "images"
KEYRING_SERVICE = "ClipMaster"
KEYRING_USER = "github-token"

EMPTY_STATE = {"version": 1, "groups": {}, "messages": {}, "settings": {}}
Remote = namedtuple("Remote", "commit tree data images")  # images: {hash: blob_sha}
MergePlan = namedtuple("MergePlan", "merged changed_local needs_push images")


class SyncError(Exception):
    pass


class SyncConflict(Exception):
    pass


# ---------------------------------------------------------------- الدمج
def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def state_hash(state):
    return hashlib.sha256(canonical(state).encode("utf-8")).hexdigest()


def normalize_state(raw):
    raw = raw or {}
    state = {"version": 1, "groups": {}, "messages": {}, "settings": {}}
    for uid, g in (raw.get("groups") or {}).items():
        state["groups"][uid] = {
            "name": str(g.get("name", "")),
            "position": int(g.get("position", 0)),
            "updated_at": int(g.get("updated_at", 0)),
            "deleted": int(bool(g.get("deleted", 0))),
        }
    for uid, m in (raw.get("messages") or {}).items():
        state["messages"][uid] = {
            "group": m.get("group"),
            "name": str(m.get("name", "")),
            "content": str(m.get("content", "")),
            "position": int(m.get("position", 0)),
            "keybind": m.get("keybind") or None,
            "starred": int(bool(m.get("starred", 0))),
            "image": m.get("image") or None,
            "updated_at": int(m.get("updated_at", 0)),
            "deleted": int(bool(m.get("deleted", 0))),
        }
    for key, s in (raw.get("settings") or {}).items():
        if key in SYNCED_SETTINGS:
            state["settings"][key] = {"value": s.get("value"), "updated_at": int(s.get("updated_at", 0))}
    return state


def _pick(a, b):
    if a is None:
        return dict(b)
    if b is None:
        return dict(a)
    key_a = (a.get("updated_at", 0), a.get("deleted", 0))
    key_b = (b.get("updated_at", 0), b.get("deleted", 0))
    if key_a != key_b:
        return dict(a if key_a > key_b else b)
    return dict(a if canonical(a) >= canonical(b) else b)


def merge_states(local, remote):
    """دمج حتمّي: نفس النتيجة بغض النظر مين المحلي ومين البعيد."""
    merged = {"version": 1, "groups": {}, "messages": {}, "settings": {}}
    for section in ("groups", "messages", "settings"):
        for uid in set(local[section]) | set(remote[section]):
            merged[section][uid] = _pick(local[section].get(uid), remote[section].get(uid))

    # رسالة داخل مجموعة اتمسحت (أو مش موجودة) تتمسح
    for message in merged["messages"].values():
        group = merged["groups"].get(message["group"])
        if group is None or group["deleted"]:
            if not message["deleted"]:
                message["deleted"] = 1
                message["updated_at"] = max(message["updated_at"], (group or {}).get("updated_at", 0))

    # الاختصار الواحد ما يتكررش: الأحدث بيكسب والأقدم يتشال منه الاختصار
    by_keybind = {}
    for uid, message in merged["messages"].items():
        if not message["deleted"] and message.get("keybind"):
            by_keybind.setdefault(message["keybind"].lower(), []).append((message["updated_at"], uid, "m"))
    for key, setting in merged["settings"].items():
        if setting.get("value"):
            by_keybind.setdefault(str(setting["value"]).lower(), []).append((setting["updated_at"], key, "s"))
    for entries in by_keybind.values():
        if len(entries) < 2:
            continue
        entries.sort()
        winner_time = entries[-1][0]
        for _, ident, kind in entries[:-1]:
            if kind == "m":
                merged["messages"][ident]["keybind"] = None
                merged["messages"][ident]["updated_at"] = winner_time + 1
            else:
                merged["settings"][ident]["value"] = None
                merged["settings"][ident]["updated_at"] = winner_time + 1
    return merged


# ---------------------------------------------------------------- GitHub
class GitHubBackend:
    def __init__(self, repo, token, base_url=API_URL, timeout=20):
        self.repo = (repo or "").strip().strip("/")
        self.token = (token or "").strip()
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._branch = None

    # -- HTTP
    def _call(self, method, path, body=None):
        url = f"{self.base}/repos/{self.repo}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ClipMaster-Sync",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw, status = response.read(), response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(), error.code
        except (urllib.error.URLError, OSError) as error:
            raise SyncError(f"تعذر الاتصال بـ GitHub: {getattr(error, 'reason', error)}") from error
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        return status, payload

    def _explain(self, status, payload):
        message = payload.get("message", "") if isinstance(payload, dict) else ""
        if status == 401:
            return "التوكن غير صالح أو منتهي"
        if status == 403:
            return "التوكن ما عندوش صلاحية Contents (قراءة وكتابة) أو تم تجاوز حد الطلبات"
        if status == 404:
            return "المستودع غير موجود أو التوكن لا يملك صلاحية الوصول إليه"
        return f"GitHub {status}: {message}"

    def _expect(self, status, payload, ok):
        if status not in ok:
            raise SyncError(self._explain(status, payload))
        return payload

    # -- العمليات
    def check(self):
        status, payload = self._call("GET", "")
        self._expect(status, payload, (200,))
        self._branch = payload.get("default_branch") or "main"
        permissions = payload.get("permissions") or {}
        return {
            "name": payload.get("full_name", self.repo),
            "private": bool(payload.get("private")),
            "can_push": permissions.get("push", True),
            "branch": self._branch,
        }

    def _branch_name(self):
        if self._branch is None:
            self.check()
        return self._branch

    def head_commit(self):
        status, payload = self._call("GET", f"/git/ref/heads/{self._branch_name()}")
        if status in (404, 409):
            return None
        self._expect(status, payload, (200,))
        return payload["object"]["sha"]

    def fetch(self):
        commit = self.head_commit()
        if commit is None:
            return Remote(None, None, None, {})
        status, payload = self._call("GET", f"/git/commits/{commit}")
        tree = self._expect(status, payload, (200,))["tree"]["sha"]
        status, payload = self._call("GET", f"/git/trees/{tree}?recursive=1")
        self._expect(status, payload, (200,))
        if payload.get("truncated"):
            raise SyncError("المستودع كبير جدًا على المزامنة")
        data_blob, images = None, {}
        for entry in payload.get("tree", []):
            path = entry.get("path", "")
            if path == DATA_PATH:
                data_blob = entry["sha"]
            elif path.startswith(IMAGES_DIR + "/") and path.endswith(".png"):
                images[path[len(IMAGES_DIR) + 1:-4]] = entry["sha"]
        data = None
        if data_blob:
            status, payload = self._call("GET", f"/git/blobs/{data_blob}")
            self._expect(status, payload, (200,))
            data = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
        return Remote(commit, tree, data, images)

    def download_images(self, remote, hashes):
        result = {}
        for digest in hashes:
            blob = remote.images.get(digest)
            if blob is None:
                raise SyncError("صورة مفقودة في المستودع")
            status, payload = self._call("GET", f"/git/blobs/{blob}")
            self._expect(status, payload, (200,))
            result[digest] = base64.b64decode(payload["content"])
        return result

    def _bootstrap(self):
        body = {"message": "init", "content": base64.b64encode(b"# ClipMaster data\n").decode()}
        status, payload = self._call("PUT", "/contents/README.md", body)
        self._expect(status, payload, (200, 201))

    def _blob(self, content_bytes):
        body = {"content": base64.b64encode(content_bytes).decode("ascii"), "encoding": "base64"}
        status, payload = self._call("POST", "/git/blobs", body)
        return self._expect(status, payload, (201,))["sha"]

    def push(self, remote, state, images):
        if remote.commit is None:
            self._bootstrap()
            remote = self.fetch()
        entries = []
        for digest, content in images.items():
            entries.append({"path": f"{IMAGES_DIR}/{digest}.png", "mode": "100644", "type": "blob",
                            "sha": self._blob(content)})
        text = json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True)
        entries.append({"path": DATA_PATH, "mode": "100644", "type": "blob", "sha": self._blob(text.encode("utf-8"))})
        status, payload = self._call("POST", "/git/trees", {"base_tree": remote.tree, "tree": entries})
        tree = self._expect(status, payload, (201,))["sha"]
        message = "sync " + time.strftime("%Y-%m-%d %H:%M:%S")
        status, payload = self._call("POST", "/git/commits",
                                     {"message": message, "tree": tree, "parents": [remote.commit]})
        commit = self._expect(status, payload, (201,))["sha"]
        status, payload = self._call("PATCH", f"/git/refs/heads/{self._branch_name()}",
                                     {"sha": commit, "force": False})
        if status == 422:
            raise SyncConflict("جهاز تاني رفع تعديلات في نفس الوقت")
        self._expect(status, payload, (200,))
        return commit


# ---------------------------------------------------------------- المحرك
class SyncEngine:
    def __init__(self, store, backend):
        self.store = store
        self.backend = backend

    def fetch_phase(self, local_hashes):
        """شبكة بس (ينفع يشتغل في thread): هات البيانات البعيدة والصور الناقصة."""
        remote = self.backend.fetch()
        images = {}
        if remote.data:
            state = normalize_state(remote.data)
            needed = {
                m["image"] for m in state["messages"].values()
                if m["image"] and not m["deleted"] and m["image"] not in local_hashes
            }
            images = self.backend.download_images(remote, sorted(needed))
            for digest, content in images.items():
                if image_digest(content) != digest:
                    raise SyncError("صورة تالفة في المستودع")
        return remote, images

    def merge_phase(self, remote, images):
        """على الـ thread الرئيسي: ادمج واكتب على قاعدة البيانات المحلية."""
        local = self.store.snapshot()
        remote_state = normalize_state(remote.data) if remote.data else normalize_state(EMPTY_STATE)
        merged = merge_states(local, remote_state)
        changed_local = state_hash(merged) != state_hash(local)
        if changed_local:
            self.store.apply_state(merged, images)
        wanted = {m["image"] for m in merged["messages"].values() if m["image"] and not m["deleted"]}
        to_push = {}
        for digest in wanted - set(remote.images):
            content = self.store.get_image_by_hash(digest)
            if content is None:
                raise SyncError("صورة محلية مفقودة")
            to_push[digest] = content
        needs_push = bool(to_push) or state_hash(merged) != state_hash(remote_state) or remote.commit is None
        return MergePlan(merged, changed_local, needs_push, to_push)

    def record(self, commit, merged):
        self.store.set_setting("sync_last_commit", commit or "")
        self.store.set_setting("sync_last_hash", state_hash(merged))
        self.store.set_setting("sync_last_time", str(now_ms()))

    def run_cycle(self, attempts=3):
        """دورة كاملة متتالية (للاختبارات والمزامنة عند الإغلاق)."""
        for _ in range(attempts):
            remote, images = self.fetch_phase(self.store.local_image_hashes())
            plan = self.merge_phase(remote, images)
            if not plan.needs_push:
                self.record(remote.commit, plan.merged)
                return {"changed_local": plan.changed_local, "pushed": False}
            try:
                commit = self.backend.push(remote, plan.merged, plan.images)
            except SyncConflict:
                continue
            self.record(commit, plan.merged)
            return {"changed_local": plan.changed_local, "pushed": True}
        raise SyncError("تعارض متكرر مع جهاز تاني، جرّب بعد شوية")


# ---------------------------------------------------------------- التوكن
def load_token(store):
    try:
        import keyring
        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if token:
            return token
    except Exception:
        pass
    return store.get_setting("sync_token_fallback") or ""


def save_token(store, token):
    """بيرجع True لو اتحفظ في مخزن كلمات السر الآمن، False لو اتحفظ كاحتياطي."""
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)
        store.set_setting("sync_token_fallback", None)
        return True
    except Exception:
        store.set_setting("sync_token_fallback", token)
        return False


# ---------------------------------------------------------------- المدير (Qt)
try:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
except Exception:  # pragma: no cover - للاختبارات من غير Qt
    QObject = object
    QTimer = None
    pyqtSignal = None


if QTimer is not None:
    class SyncManager(QObject):
        status_changed = pyqtSignal(str, str)   # state: off | syncing | ok | error
        data_changed = pyqtSignal()
        _fetched = pyqtSignal(object, object, object)
        _pushed = pyqtSignal(object, object)

        def __init__(self, store, backend_factory=None, parent=None):
            super().__init__(parent)
            self.store = store
            self.backend_factory = backend_factory
            self.state = "off"
            self.message = ""
            self._running = False
            self._pending = False
            self._attempt = 0
            self._force = True
            self._remote = None
            self._plan = None
            self._engine = None
            self._debounce = QTimer(self)
            self._debounce.setSingleShot(True)
            self._debounce.timeout.connect(lambda: self.sync_now("changes", force=False))
            self._fetched.connect(self._on_fetched)
            self._pushed.connect(self._on_pushed)

        # -- إعدادات
        def configured(self):
            return bool(self.store.get_setting("sync_repo")) and bool(load_token(self.store))

        def make_backend(self, timeout=20):
            if self.backend_factory is not None:
                return self.backend_factory()
            return GitHubBackend(self.store.get_setting("sync_repo"), load_token(self.store), timeout=timeout)

        def last_sync_ms(self):
            try:
                return int(self.store.get_setting("sync_last_time") or 0)
            except ValueError:
                return 0

        def is_dirty(self):
            return state_hash(self.store.snapshot()) != self.store.get_setting("sync_last_hash")

        def _set(self, state, message=""):
            self.state, self.message = state, message
            self.status_changed.emit(state, message)

        # -- التشغيل
        def schedule(self, delay_ms=8000):
            if self.configured():
                self._debounce.start(delay_ms)

        def sync_now(self, reason="manual", force=True):
            if not self.configured():
                self._set("off", "المزامنة غير مفعّلة")
                return
            if self._running:
                self._pending = True
                return
            self._running = True
            self._attempt = 0
            self._force = force
            self._set("syncing", "جاري المزامنة...")
            self._start_fetch()

        def _start_fetch(self):
            try:
                backend = self.make_backend()
            except Exception as error:
                self._finish("error", str(error))
                return
            self._engine = SyncEngine(self.store, backend)
            local_hashes = self.store.local_image_hashes()
            dirty = self.is_dirty()
            last_commit = self.store.get_setting("sync_last_commit") or None
            force, engine = self._force, self._engine

            def work():
                try:
                    if not force and not dirty:
                        head = backend.head_commit()
                        if head is not None and head == last_commit:
                            self._fetched.emit(None, {}, None)
                            return
                    remote, images = engine.fetch_phase(local_hashes)
                    self._fetched.emit(remote, images, None)
                except Exception as error:  # noqa: BLE001
                    self._fetched.emit(None, {}, error)

            threading.Thread(target=work, daemon=True).start()

        def _on_fetched(self, remote, images, error):
            if error is not None:
                self._finish("error", self._describe(error))
                return
            if remote is None:
                self._finish("ok", "")
                return
            try:
                plan = self._engine.merge_phase(remote, images)
            except Exception as exc:  # noqa: BLE001
                self._finish("error", self._describe(exc))
                return
            if plan.changed_local:
                self.data_changed.emit()
            if not plan.needs_push:
                self._engine.record(remote.commit, plan.merged)
                self._finish("ok", "")
                return
            self._remote, self._plan = remote, plan
            backend, engine = self._engine.backend, self._engine

            def work():
                try:
                    commit = backend.push(remote, plan.merged, plan.images)
                    self._pushed.emit(commit, None)
                except Exception as exc:  # noqa: BLE001
                    self._pushed.emit(None, exc)

            threading.Thread(target=work, daemon=True).start()

        def _on_pushed(self, commit, error):
            if isinstance(error, SyncConflict):
                self._attempt += 1
                if self._attempt < 3:
                    self._force = True
                    self._start_fetch()
                    return
                self._finish("error", "تعارض متكرر مع جهاز تاني، جرّب بعد شوية")
                return
            if error is not None:
                self._finish("error", self._describe(error))
                return
            self._engine.record(commit, self._plan.merged)
            self._finish("ok", "")

        def _describe(self, error):
            return str(error) if isinstance(error, SyncError) else f"خطأ غير متوقع: {error}"

        def _finish(self, state, message):
            self._running = False
            self._set(state, message)
            if self._pending:
                self._pending = False
                QTimer.singleShot(1500, lambda: self.sync_now("pending", force=False))

        def sync_blocking(self, timeout=8):
            """مزامنة متتالية قبل الإغلاق (بتتجاهل الأخطاء)."""
            if not self.configured() or self._running:
                return
            try:
                if not self.is_dirty():
                    return
                SyncEngine(self.store, self.make_backend(timeout=timeout)).run_cycle()
            except Exception as error:  # noqa: BLE001
                print(f"[ebo-clipboard] final sync failed: {error}")
