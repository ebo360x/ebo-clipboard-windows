"""قراءة النسخ الاحتياطية بتاعة برنامج Ditto.

نسخة Ditto الاحتياطية عبارة عن قاعدة SQLite (أحيانًا مضغوطة gzip زي ملفات
الـ backup اللي بيعملها). كل رسالة في جدول Main ومحتواها بصيغ الحافظة في جدول Data.
"""
import gzip
import os
import sqlite3
import struct
import tempfile

SQLITE_MAGIC = b"SQLite format 3\x00"
GZIP_MAGIC = b"\x1f\x8b"
DEFAULT_GROUP_NAME = "Ditto"
NAME_MAX_LENGTH = 40

# فلاجات HOTKEYF اللي Ditto بيخزنها في البايت التاني من الاختصار
_DITTO_SHIFT = 0x01
_DITTO_CTRL = 0x02
_DITTO_ALT = 0x04
_DITTO_WIN = 0x08

# البرنامج بيسجل الأرقام والعلامات بالـ scan code علشان يفرق بين النمباد والصف العلوي
_VK_TO_SCAN = {
    **{0x30 + d: (11 if d == 0 else d + 1) for d in range(10)},  # 0-9 الصف العلوي
    0x60: 0x52, 0x61: 0x4F, 0x62: 0x50, 0x63: 0x51, 0x64: 0x4B,
    0x65: 0x4C, 0x66: 0x4D, 0x67: 0x47, 0x68: 0x48, 0x69: 0x49,  # نمباد 0-9
    0x6A: 0x37, 0x6B: 0x4E, 0x6D: 0x4A, 0x6E: 0x53, 0x6F: 0x135,  # نمباد * + - . /
    0xBD: 0x0C, 0xBB: 0x0D, 0xBE: 0x34, 0xBF: 0x35,  # - = . / الصف العلوي
}
_VK_TO_NAME = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x1B: "esc", 0x20: "space",
    0x21: "page up", 0x22: "page down", 0x23: "end", 0x24: "home",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x2D: "insert", 0x2E: "delete",
    0xBC: "comma", 0xBA: "semicolon", 0xDE: "quote", 0xC0: "backtick",
    0xDB: "left bracket", 0xDD: "right bracket", 0xDC: "backslash",
}


def is_ditto_backup(path):
    with open(path, "rb") as f:
        head = f.read(len(SQLITE_MAGIC))
    return head.startswith(GZIP_MAGIC) or head == SQLITE_MAGIC


def ditto_hotkey_to_combo(value):
    """تحويل اختصار Ditto (رقم) لصيغة البرنامج زي "ctrl+f1" أو "ctrl+scan:83"."""
    if not value:
        return None
    vk = value & 0xFF
    mods = (value >> 8) & 0xFF
    if 0x41 <= vk <= 0x5A:
        key = chr(vk).lower()
    elif 0x70 <= vk <= 0x87:
        key = f"f{vk - 0x6F}"
    elif vk in _VK_TO_SCAN:
        key = f"scan:{_VK_TO_SCAN[vk]}"
    elif vk in _VK_TO_NAME:
        key = _VK_TO_NAME[vk]
    else:
        return None
    parts = []
    if mods & _DITTO_CTRL:
        parts.append("ctrl")
    if mods & _DITTO_ALT:
        parts.append("alt")
    if mods & _DITTO_SHIFT:
        parts.append("shift")
    if mods & _DITTO_WIN:
        parts.append("windows")
    parts.append(key)
    return "+".join(parts)


def _decode_unicode(blob):
    text = bytes(blob).decode("utf-16-le", errors="replace")
    return text.split("\x00", 1)[0]


def _decode_ansi(blob):
    raw = bytes(blob).split(b"\x00", 1)[0]
    for encoding in ("cp1256", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _dib_to_png(blob):
    """CF_DIB مفيهوش هيدر ملف BMP، فبنضيفه ونحوّل لـ PNG زي باقي صور البرنامج."""
    data = bytes(blob)
    if len(data) < 40:
        return None
    header_size, = struct.unpack_from("<I", data, 0)
    bit_count, = struct.unpack_from("<H", data, 14)
    compression, = struct.unpack_from("<I", data, 16)
    colors_used, = struct.unpack_from("<I", data, 32)
    palette = colors_used or (1 << bit_count if bit_count <= 8 else 0)
    if compression == 3 and header_size == 40:  # BI_BITFIELDS
        palette = 3
    offset = 14 + header_size + palette * 4
    bmp = b"BM" + struct.pack("<IHHI", 14 + len(data), 0, 0, offset) + data
    try:
        from PyQt6.QtCore import QBuffer, QIODevice
        from PyQt6.QtGui import QImage
    except ImportError:
        return None
    image = QImage.fromData(bmp, "BMP")
    if image.isNull():
        return None
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        return None
    return bytes(buffer.data())


def _clip_name(quick_paste, text):
    if quick_paste and quick_paste.strip():
        return quick_paste.strip()
    for line in text.splitlines():
        line = line.strip().strip('"').strip()
        if line:
            return line if len(line) <= NAME_MAX_LENGTH else line[:NAME_MAX_LENGTH].rstrip() + "…"
    return "رسالة من Ditto"


def _open_database(path):
    """بيرجع (مسار SQLite، مسار مؤقت لازم يتمسح أو None)."""
    with open(path, "rb") as f:
        head = f.read(2)
    if head != GZIP_MAGIC:
        return path, None
    fd, temp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as out, gzip.open(path, "rb") as src:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
    except Exception:
        os.remove(temp_path)
        raise
    return temp_path, temp_path


def read_ditto_backup(path):
    """بيرجع قائمة مجموعات: [{"name": ..., "messages": [{"name", "content", "keybind", "image"}]}]."""
    db_path, temp_path = _open_database(path)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"Main", "Data"} <= tables:
                raise ValueError("الملف ده مش نسخة احتياطية من Ditto")
            return _read_clips(conn)
        finally:
            conn.close()
    finally:
        if temp_path:
            os.remove(temp_path)


def _read_clips(conn):
    formats = {}
    for parent_id, fmt, blob in conn.execute(
        "SELECT lParentID, strClipBoardFormat, ooData FROM Data WHERE ooData IS NOT NULL"
    ):
        formats.setdefault(parent_id, {})[fmt] = blob

    rows = conn.execute(
        "SELECT lID, mText, lShortCut, globalShortCut, QuickPasteText, bIsGroup, lParentID "
        "FROM Main ORDER BY stickyClipOrder DESC, clipOrder DESC, lID DESC"
    ).fetchall()
    group_names = {row[0]: (row[1] or "").strip() or f"Ditto {row[0]}" for row in rows if row[5]}
    groups = {}  # المفتاح: lID بتاع مجموعة Ditto أو None للرسائل اللي برا أي مجموعة
    for clip_id, m_text, shortcut, is_global, quick_paste, is_group, parent_id in rows:
        if is_group:
            continue
        clip_formats = formats.get(clip_id, {})
        if "CF_UNICODETEXT" in clip_formats:
            text = _decode_unicode(clip_formats["CF_UNICODETEXT"])
        elif "CF_TEXT" in clip_formats:
            text = _decode_ansi(clip_formats["CF_TEXT"])
        else:
            text = m_text or ""
        image = _dib_to_png(clip_formats["CF_DIB"]) if "CF_DIB" in clip_formats else None
        if not text.strip() and image is None:
            continue
        # اختصارات Ditto غير العامة بتشتغل بس وهو مفتوح، فمش بننقلها كاختصارات عامة
        keybind = ditto_hotkey_to_combo(shortcut) if is_global else None
        key = parent_id if parent_id in group_names else None
        groups.setdefault(key, []).append({
            "name": _clip_name(quick_paste, text if text.strip() else ""),
            "content": text,
            "keybind": keybind,
            "image": image,
        })

    result = []
    if None in groups:
        result.append({"name": DEFAULT_GROUP_NAME, "messages": groups.pop(None)})
    for group_id, messages in groups.items():
        result.append({"name": group_names[group_id], "messages": messages})
    return result
