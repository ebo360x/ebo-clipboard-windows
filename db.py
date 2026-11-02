import sqlite3
import os
import json
import base64
import hashlib
import time
import uuid

DB_PATH = os.path.expanduser("~/.config/ebo-clipboard/data.db")
SHEETS_CACHE_TTL_SECONDS = 15 * 60
_UNSET = object()

# إعدادات بتتزامن بين الأجهزة (باقي الإعدادات محلية)
SYNCED_SETTINGS = ("toggle_window_keybind",)
_LEGACY_NAMESPACE = uuid.UUID("6f1c2f0e-5d0c-4a52-9a53-3b0f6f5a8c11")


def now_ms():
    return int(time.time() * 1000)


def new_uid():
    return uuid.uuid4().hex


def image_digest(data):
    return hashlib.sha256(data).hexdigest() if data else None


def _ensure_column(conn, table, name, definition):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _backfill_sync_columns(conn):
    # البيانات القديمة بتاخد معرّف ثابت مشتق من الأسماء، فلو نفس البيانات موجودة
    # على جهازين هتتطابق من غير ما تتكرر. وقت التعديل 0 يعني "أقدم من أي تعديل حقيقي".
    seen_groups = {}
    for group_id, name in conn.execute("SELECT id, name FROM groups WHERE uid IS NULL ORDER BY id").fetchall():
        occurrence = seen_groups.get(name, 0)
        seen_groups[name] = occurrence + 1
        uid = uuid.uuid5(_LEGACY_NAMESPACE, f"g|{name}|{occurrence}").hex
        conn.execute("UPDATE groups SET uid = ? WHERE id = ?", (uid, group_id))
    seen_messages = {}
    rows = conn.execute(
        "SELECT m.id, g.uid, m.name FROM messages m LEFT JOIN groups g ON g.id = m.group_id "
        "WHERE m.uid IS NULL ORDER BY m.id"
    ).fetchall()
    for message_id, group_uid, name in rows:
        if group_uid is None:
            uid = new_uid()
        else:
            key = (group_uid, name)
            occurrence = seen_messages.get(key, 0)
            seen_messages[key] = occurrence + 1
            uid = uuid.uuid5(_LEGACY_NAMESPACE, f"m|{group_uid}|{name}|{occurrence}").hex
        conn.execute("UPDATE messages SET uid = ? WHERE id = ?", (uid, message_id))
    for message_id, image in conn.execute(
        "SELECT id, image FROM messages WHERE image IS NOT NULL AND image_hash IS NULL"
    ).fetchall():
        conn.execute("UPDATE messages SET image_hash = ? WHERE id = ?", (image_digest(bytes(image)), message_id))


def get_conn(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        keybind TEXT,
        FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sheets_cache (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    _ensure_column(conn, "messages", "flagged", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "messages", "image", "BLOB")
    _ensure_column(conn, "messages", "starred", "INTEGER NOT NULL DEFAULT 0")
    for table in ("groups", "messages"):
        _ensure_column(conn, table, "uid", "TEXT")
        _ensure_column(conn, table, "updated_at", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, table, "deleted", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "messages", "image_hash", "TEXT")
    _backfill_sync_columns(conn)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_uid ON groups(uid)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_uid ON messages(uid)")
    conn.commit()
    return conn


_MESSAGE_COLUMNS = "id, group_id, name, content, position, keybind, flagged, image IS NOT NULL, starred"


class Store:
    def __init__(self, path=None):
        self.path = path or DB_PATH
        self.conn = get_conn(self.path)
        self._listeners = []

    # ---------- تغييرات محلية (بتشغّل المزامنة) ----------
    def add_change_listener(self, callback):
        self._listeners.append(callback)

    def _commit(self, notify=True):
        self.conn.commit()
        if notify:
            for callback in list(self._listeners):
                callback()

    # ---------- المجموعات ----------
    def list_groups(self):
        cur = self.conn.execute(
            "SELECT id, name, position FROM groups WHERE deleted = 0 ORDER BY position, uid"
        )
        return [{"id": r[0], "name": r[1], "position": r[2]} for r in cur.fetchall()]

    def add_group(self, name):
        cur = self.conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM groups WHERE deleted = 0")
        pos = cur.fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO groups (name, position, uid, updated_at) VALUES (?, ?, ?, ?)",
            (name, pos, new_uid(), now_ms()),
        )
        self._commit()
        return cur.lastrowid

    def rename_group(self, group_id, name):
        self.conn.execute("UPDATE groups SET name = ?, updated_at = ? WHERE id = ?", (name, now_ms(), group_id))
        self._commit()

    def delete_group(self, group_id):
        stamp = now_ms()
        self.conn.execute(
            "UPDATE messages SET deleted = 1, updated_at = ? WHERE group_id = ? AND deleted = 0", (stamp, group_id)
        )
        self.conn.execute("UPDATE groups SET deleted = 1, updated_at = ? WHERE id = ?", (stamp, group_id))
        self._commit()

    def reorder_groups(self, ordered_ids):
        stamp = now_ms()
        for pos, gid in enumerate(ordered_ids):
            self.conn.execute(
                "UPDATE groups SET position = ?, updated_at = ? WHERE id = ? AND position != ?",
                (pos, stamp, gid, pos),
            )
        self._commit()

    # ---------- الرسائل ----------
    def list_messages(self, group_id):
        cur = self.conn.execute(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE group_id = ? AND deleted = 0 ORDER BY position, uid",
            (group_id,),
        )
        return [self._row_to_msg(r) for r in cur.fetchall()]

    def list_all_messages(self):
        cur = self.conn.execute(
            "SELECT m.id, m.group_id, m.name, m.content, m.position, m.keybind, m.flagged, m.image IS NOT NULL, m.starred "
            "FROM messages m JOIN groups g ON g.id = m.group_id WHERE m.deleted = 0 AND g.deleted = 0"
        )
        return [self._row_to_msg(r) for r in cur.fetchall()]

    def _row_to_msg(self, r):
        return {
            "id": r[0], "group_id": r[1], "name": r[2], "content": r[3], "position": r[4],
            "keybind": r[5], "flagged": bool(r[6]), "has_image": bool(r[7]), "starred": bool(r[8]),
        }

    def get_message_image(self, msg_id):
        cur = self.conn.execute("SELECT image FROM messages WHERE id = ?", (msg_id,))
        row = cur.fetchone()
        return row[0] if row else None

    def add_message(self, group_id, name, content, image=None):
        cur = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM messages WHERE group_id = ? AND deleted = 0", (group_id,)
        )
        pos = cur.fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO messages (group_id, name, content, position, image, uid, updated_at, image_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (group_id, name, content, pos, image, new_uid(), now_ms(), image_digest(image)),
        )
        self._commit()
        return cur.lastrowid

    def update_message(self, msg_id, name=None, content=None, image=_UNSET):
        stamp = now_ms()
        if name is not None:
            self.conn.execute("UPDATE messages SET name = ?, updated_at = ? WHERE id = ?", (name, stamp, msg_id))
        if content is not None:
            self.conn.execute("UPDATE messages SET content = ?, updated_at = ? WHERE id = ?", (content, stamp, msg_id))
        if image is not _UNSET:
            self.conn.execute(
                "UPDATE messages SET image = ?, image_hash = ?, updated_at = ? WHERE id = ?",
                (image, image_digest(image), stamp, msg_id),
            )
        self._commit()

    def delete_message(self, msg_id):
        self.conn.execute("UPDATE messages SET deleted = 1, updated_at = ? WHERE id = ?", (now_ms(), msg_id))
        self._commit()

    def reorder_messages(self, group_id, ordered_ids):
        stamp = now_ms()
        for pos, mid in enumerate(ordered_ids):
            self.conn.execute(
                "UPDATE messages SET position = ?, group_id = ?, updated_at = ? "
                "WHERE id = ? AND (position != ? OR group_id != ?)",
                (pos, group_id, stamp, mid, pos, group_id),
            )
        self._commit()

    def move_message(self, msg_id, new_group_id):
        cur = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM messages WHERE group_id = ? AND deleted = 0", (new_group_id,)
        )
        pos = cur.fetchone()[0]
        self.conn.execute(
            "UPDATE messages SET group_id = ?, position = ?, updated_at = ? WHERE id = ?",
            (new_group_id, pos, now_ms(), msg_id),
        )
        self._commit()

    def set_keybind(self, msg_id, keybind):
        self.conn.execute("UPDATE messages SET keybind = ?, updated_at = ? WHERE id = ?", (keybind, now_ms(), msg_id))
        self._commit()

    def set_starred(self, msg_id, starred):
        self.conn.execute(
            "UPDATE messages SET starred = ?, updated_at = ? WHERE id = ?", (1 if starred else 0, now_ms(), msg_id)
        )
        self._commit()

    def set_flagged(self, msg_id, flagged):
        # علامة التعارض محلية بس ومش بتتزامن
        self.conn.execute("UPDATE messages SET flagged = ? WHERE id = ?", (1 if flagged else 0, msg_id))
        self._commit(notify=False)

    # ---------- الإعدادات ----------
    def get_setting(self, key, default=None):
        cur = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

    def _write_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )

    def set_setting(self, key, value):
        self._write_setting(key, value)
        synced = key in SYNCED_SETTINGS
        if synced:
            self._write_setting("__updated:" + key, str(now_ms()))
        self._commit(notify=synced)

    # ---------- المزامنة ----------
    def snapshot(self):
        groups = {}
        for uid, name, position, updated_at, deleted in self.conn.execute(
            "SELECT uid, name, position, updated_at, deleted FROM groups"
        ):
            groups[uid] = {"name": name, "position": position, "updated_at": updated_at, "deleted": deleted}
        messages = {}
        for row in self.conn.execute(
            "SELECT m.uid, g.uid, m.name, m.content, m.position, m.keybind, m.starred, m.image_hash, "
            "m.updated_at, m.deleted FROM messages m JOIN groups g ON g.id = m.group_id"
        ):
            messages[row[0]] = {
                "group": row[1], "name": row[2], "content": row[3], "position": row[4],
                "keybind": row[5] or None, "starred": row[6], "image": row[7],
                "updated_at": row[8], "deleted": row[9],
            }
        settings = {}
        for key in SYNCED_SETTINGS:
            updated = int(self.get_setting("__updated:" + key, 0) or 0)
            value = self.get_setting(key)
            if updated or value is not None:
                settings[key] = {"value": value, "updated_at": updated}
        return {"version": 1, "groups": groups, "messages": messages, "settings": settings}

    def local_image_hashes(self):
        return {
            row[0]
            for row in self.conn.execute(
                "SELECT DISTINCT image_hash FROM messages WHERE image_hash IS NOT NULL AND image IS NOT NULL"
            )
        }

    def get_image_by_hash(self, digest):
        row = self.conn.execute(
            "SELECT image FROM messages WHERE image_hash = ? AND image IS NOT NULL LIMIT 1", (digest,)
        ).fetchone()
        return bytes(row[0]) if row else None

    def apply_state(self, state, images=None):
        images = images or {}
        c = self.conn
        group_ids = {uid: gid for gid, uid in c.execute("SELECT id, uid FROM groups")}
        for uid, g in state["groups"].items():
            new_values = (g["name"], g["position"], g["updated_at"], g["deleted"])
            if uid in group_ids:
                current = c.execute(
                    "SELECT name, position, updated_at, deleted FROM groups WHERE uid = ?", (uid,)
                ).fetchone()
                if tuple(current) != new_values:
                    c.execute(
                        "UPDATE groups SET name = ?, position = ?, updated_at = ?, deleted = ? WHERE uid = ?",
                        new_values + (uid,),
                    )
            else:
                cur = c.execute(
                    "INSERT INTO groups (name, position, updated_at, deleted, uid) VALUES (?, ?, ?, ?, ?)",
                    new_values + (uid,),
                )
                group_ids[uid] = cur.lastrowid
        for uid, m in state["messages"].items():
            group_id = group_ids.get(m["group"])
            if group_id is None:
                continue
            current = c.execute(
                "SELECT group_id, name, content, position, keybind, starred, image_hash, updated_at, deleted "
                "FROM messages WHERE uid = ?",
                (uid,),
            ).fetchone()
            new_values = (
                group_id, m["name"], m["content"], m["position"], m.get("keybind"),
                m["starred"], m.get("image"), m["updated_at"], m["deleted"],
            )
            if current is None:
                image = images.get(m.get("image")) if m.get("image") else None
                c.execute(
                    "INSERT INTO messages (group_id, name, content, position, keybind, starred, image_hash, "
                    "updated_at, deleted, uid, image) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    new_values + (uid, image),
                )
            elif tuple(current) != new_values:
                image_changed = current[6] != m.get("image")
                c.execute(
                    "UPDATE messages SET group_id = ?, name = ?, content = ?, position = ?, keybind = ?, "
                    "starred = ?, image_hash = ?, updated_at = ?, deleted = ? WHERE uid = ?",
                    new_values + (uid,),
                )
                if image_changed:
                    image = images.get(m.get("image")) if m.get("image") else None
                    c.execute("UPDATE messages SET image = ? WHERE uid = ?", (image, uid))
        for key, setting in state["settings"].items():
            if key in SYNCED_SETTINGS:
                self._write_setting(key, setting.get("value"))
                self._write_setting("__updated:" + key, str(setting.get("updated_at", 0)))
        self._commit(notify=False)

    # ---------- الكاش ----------
    def get_cached_data(self, key):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            row = conn.execute(
                "SELECT value FROM sheets_cache WHERE key = ?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else None

    def set_cached_data(self, key, value):
        serialized = json.dumps(value, ensure_ascii=False)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute(
                "INSERT INTO sheets_cache (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, serialized),
            )
            conn.commit()
        finally:
            conn.close()

    # ---------- التصدير والاستيراد ----------
    def export_zdb(self, path):
        groups = self.list_groups()
        data = {"groups": []}
        for g in groups:
            msgs = self.list_messages(g["id"])
            data["groups"].append({
                "name": g["name"],
                "messages": [{
                    "name": m["name"],
                    "content": m["content"],
                    "keybind": m["keybind"],
                    "starred": m["starred"],
                    **({
                        "image": base64.b64encode(self.get_message_image(m["id"])).decode("ascii")
                    } if m["has_image"] else {}),
                } for m in msgs]
            })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def import_zdb(self, path, merge=True):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not merge:
            stamp = now_ms()
            self.conn.execute("UPDATE messages SET deleted = 1, updated_at = ? WHERE deleted = 0", (stamp,))
            self.conn.execute("UPDATE groups SET deleted = 1, updated_at = ? WHERE deleted = 0", (stamp,))
            self.conn.commit()
        for g in data.get("groups", []):
            gid = self.add_group(g["name"])
            for m in g.get("messages", []):
                image = base64.b64decode(m["image"], validate=True) if m.get("image") else None
                mid = self.add_message(gid, m["name"], m.get("content", ""), image=image)
                if m.get("keybind"):
                    self.set_keybind(mid, m["keybind"])
                if m.get("starred"):
                    self.set_starred(mid, True)
