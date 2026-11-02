import ctypes
import string
import sys
import threading
from ctypes import wintypes

import keyboard
from PyQt6.QtCore import QAbstractNativeEventFilter
from PyQt6.QtWidgets import QApplication

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
MAPVK_VSC_TO_VK_EX = 3

WINDOWS_VK_MAP = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
    "space": 0x20, "page up": 0x21, "page down": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "insert": 0x2D, "delete": 0x2E,
    "minus": 0xBD, "plus": 0xBB, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "semicolon": 0xBA, "quote": 0xDE,
    "backtick": 0xC0, "left bracket": 0xDB, "right bracket": 0xDD,
    "backslash": 0xDC,
}
WINDOWS_MODIFIER_MAP = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "windows": MOD_WIN,
}
WINDOWS_NUMPAD_SCAN_MAP = {
    0x37: 0x6A, 0x4A: 0x6D, 0x4E: 0x6B,
    0x47: 0x67, 0x48: 0x68, 0x49: 0x69,
    0x4B: 0x64, 0x4C: 0x65, 0x4D: 0x66,
    0x4F: 0x61, 0x50: 0x62, 0x51: 0x63,
    0x52: 0x60, 0x53: 0x6E,
}


def windows_hotkey_parts(combo):
    parts = combo.split("+")
    if not parts or any(part not in WINDOWS_MODIFIER_MAP for part in parts[:-1]):
        return None
    key = parts[-1]
    virtual_key = WINDOWS_VK_MAP.get(key)
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        virtual_key = 0x6F + int(key[1:])
    if virtual_key is None and len(key) == 1 and key.isascii() and key.isalnum():
        virtual_key = ord(key.upper())
    if virtual_key is None:
        return None
    modifiers = MOD_NOREPEAT
    for modifier in parts[:-1]:
        modifiers |= WINDOWS_MODIFIER_MAP[modifier]
    return modifiers, virtual_key


class WindowsHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, listener):
        super().__init__()
        self.listener = listener

    def nativeEventFilter(self, event_type, message):
        if event_type not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, message
        native_message = ctypes.cast(
            int(message), ctypes.POINTER(wintypes.MSG)
        ).contents
        if native_message.message != WM_HOTKEY:
            return False, message
        action = self.listener._native_hotkeys.get(int(native_message.wParam))
        if action is None:
            return False, message
        self.listener.on_combo(action)
        return True, message

LEGACY_KEY_MAP = {
    "KEY_LEFTCTRL": "ctrl", "KEY_RIGHTCTRL": "ctrl",
    "KEY_LEFTSHIFT": "shift", "KEY_RIGHTSHIFT": "shift",
    "KEY_LEFTALT": "alt", "KEY_RIGHTALT": "alt",
    "KEY_LEFTMETA": "windows", "KEY_RIGHTMETA": "windows",
    "KEY_ENTER": "enter", "KEY_KPENTER": "enter",
    "KEY_BACKSPACE": "backspace",
    "KEY_TAB": "tab",
    "KEY_ESC": "esc",
    "KEY_SPACE": "space",
    "KEY_DELETE": "delete",
    "KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right",
    "KEY_HOME": "home", "KEY_END": "end",
    "KEY_PAGEUP": "page up", "KEY_PAGEDOWN": "page down",
    "KEY_INSERT": "insert",
}
for i in range(1, 25):
    LEGACY_KEY_MAP[f"KEY_F{i}"] = f"f{i}"
for i in range(10):
    LEGACY_KEY_MAP[f"KEY_{i}"] = str(i)
for c in string.ascii_uppercase:
    LEGACY_KEY_MAP[f"KEY_{c}"] = c.lower()


def convert_legacy_combo(combo):
    parts = [p for p in combo.split("+") if p]
    converted = [LEGACY_KEY_MAP.get(p, p.lower()) for p in parts]
    return "+".join(converted)


class HotkeyListener:
    def __init__(self, on_combo):
        self.on_combo = on_combo
        self.bindings = {}
        self._registered_keyboard = set()
        self._single_key_hooks = []
        self._scan_bindings = {}
        self._lock = threading.Lock()
        self._paused_bindings = None
        self._global_hook = None
        self._native_app = None
        self._native_filter = None
        self._native_hotkeys = {}
        self._native_user32 = None

    def _install_native_hotkey_filter(self):
        if sys.platform != "win32" or self._native_filter is not None:
            return
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("A QApplication is required to register Windows hotkeys")
        self._native_user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._native_user32.RegisterHotKey.argtypes = (
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
        )
        self._native_user32.RegisterHotKey.restype = wintypes.BOOL
        self._native_user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        self._native_user32.UnregisterHotKey.restype = wintypes.BOOL
        self._native_user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        self._native_user32.MapVirtualKeyW.restype = wintypes.UINT
        self._native_filter = WindowsHotkeyFilter(self)
        self._native_app = app
        app.installNativeEventFilter(self._native_filter)

    def _unregister_native_hotkeys(self):
        if self._native_user32 is not None:
            for hotkey_id in list(self._native_hotkeys):
                if not self._native_user32.UnregisterHotKey(None, hotkey_id):
                    error = ctypes.WinError(ctypes.get_last_error())
                    print(f"[ebo-clipboard] failed to unregister Windows hotkey {hotkey_id}: {error}")
        self._native_hotkeys = {}

    def _register_native_hotkey(self, combo, action):
        if self._native_user32 is None:
            return False
        parts = combo.split("+")
        scan_part = next((part for part in parts if part.startswith("scan:")), None)
        if scan_part:
            try:
                scan_code = int(scan_part.split(":", 1)[1])
            except ValueError:
                return False
            modifiers = MOD_NOREPEAT
            for modifier in parts:
                if modifier == scan_part:
                    continue
                if modifier not in WINDOWS_MODIFIER_MAP:
                    return False
                modifiers |= WINDOWS_MODIFIER_MAP[modifier]
            virtual_key = WINDOWS_NUMPAD_SCAN_MAP.get(scan_code)
            if virtual_key is None:
                virtual_key = self._native_user32.MapVirtualKeyW(
                    scan_code, MAPVK_VSC_TO_VK_EX
                )
            if not virtual_key:
                return False
        else:
            hotkey = windows_hotkey_parts(combo)
            if hotkey is None:
                return False
            modifiers, virtual_key = hotkey
        hotkey_id = next(
            i for i in range(1, 0xBFFF) if i not in self._native_hotkeys
        )
        if not self._native_user32.RegisterHotKey(
            None, hotkey_id, modifiers, virtual_key
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            print(f"[ebo-clipboard] Windows hotkey registration failed for '{combo}': {error}")
            return False
        self._native_hotkeys[hotkey_id] = action
        print(f"[ebo-clipboard] registered Windows hotkey: {combo} -> {action}")
        return True

    def _on_global_event(self, event):
        if event.event_type != keyboard.KEY_DOWN:
            return True
        if event.name == "tab" and keyboard.is_pressed("alt"):
            return True
        candidates = self._scan_bindings.get(event.scan_code)
        if not candidates:
            return True
        mods_now = frozenset(m for m in ("ctrl", "alt", "shift", "windows") if keyboard.is_pressed(m))
        for required_mods, action in candidates:
            if required_mods == mods_now:
                self.on_combo(action)
                return False
        return True

    def set_bindings(self, bindings):
        with self._lock:
            self._unregister_native_hotkeys()
            for combo in list(self._registered_keyboard):
                try:
                    keyboard.remove_hotkey(combo)
                except (KeyError, ValueError):
                    pass
            self._registered_keyboard = set()
            for hook in self._single_key_hooks:
                try:
                    keyboard.unhook(hook)
                except Exception:
                    pass
            self._single_key_hooks = []
            self._scan_bindings = {}

            self.bindings = {}
            for raw_combo, action in bindings.items():
                combo = convert_legacy_combo(raw_combo)
                if not combo:
                    continue
                self.bindings[combo] = action
                parts = combo.split("+")
                scan_part = next((p for p in parts if p.startswith("scan:")), None)
                if scan_part:
                    try:
                        scan_code = int(scan_part.split(":", 1)[1])
                    except ValueError:
                        print(f"[ebo-clipboard] invalid scan token in '{combo}'")
                        continue
                    if not self._register_native_hotkey(combo, action):
                        mods = frozenset(p for p in parts if p != scan_part)
                        self._scan_bindings.setdefault(scan_code, []).append((mods, action))
                        print(f"[ebo-clipboard] registered scan-code binding: {combo} -> {action}")
                    continue
                mods = parts[:-1]
                main = parts[-1]
                if not mods:
                    if self._register_native_hotkey(combo, action):
                        continue
                    print(f"[ebo-clipboard] registering single-key binding: {combo} -> {action}")
                    self._register_single_key(main, action)
                else:
                    if self._register_native_hotkey(combo, action):
                        continue
                    try:
                        keyboard.add_hotkey(combo, lambda a=action: self.on_combo(a), suppress=True)
                        self._registered_keyboard.add(combo)
                        print(f"[ebo-clipboard] registered keyboard hotkey: {combo} -> {action}")
                    except Exception as e:
                        print(f"[ebo-clipboard] failed to register hotkey '{combo}': {e}")

            if self._scan_bindings and self._global_hook is None:
                self._global_hook = keyboard.hook(self._on_global_event, suppress=True)
            elif not self._scan_bindings and self._global_hook is not None:
                keyboard.unhook(self._global_hook)
                self._global_hook = None

    def _register_single_key(self, key_name, action):
        def handler(event):
            if event.event_type == keyboard.KEY_UP:
                return True
            if key_name == "tab" and keyboard.is_pressed("alt"):
                return True
            self.on_combo(action)
            return False

        try:
            hook = keyboard.hook_key(key_name, handler, suppress=True)
            self._single_key_hooks.append(hook)
        except Exception as e:
            print(f"[ebo-clipboard] failed to register single key '{key_name}': {e}")

    def pause(self):
        with self._lock:
            self._paused_bindings = dict(self.bindings)
        self.set_bindings({})

    def resume(self):
        if self._paused_bindings is not None:
            saved = self._paused_bindings
            self._paused_bindings = None
            self.set_bindings(saved)

    def start(self):
        self._install_native_hotkey_filter()
        self.set_bindings(self.bindings)

    def stop(self):
        with self._lock:
            self._unregister_native_hotkeys()
            for combo in list(self._registered_keyboard):
                try:
                    keyboard.remove_hotkey(combo)
                except (KeyError, ValueError):
                    pass
            self._registered_keyboard = set()
            for hook in self._single_key_hooks:
                try:
                    keyboard.unhook(hook)
                except Exception:
                    pass
            self._single_key_hooks = []
            self._scan_bindings = {}
            if self._global_hook is not None:
                try:
                    keyboard.unhook(self._global_hook)
                except Exception:
                    pass
                self._global_hook = None
            if self._native_app is not None and self._native_filter is not None:
                self._native_app.removeNativeEventFilter(self._native_filter)
            self._native_app = None
            self._native_filter = None
            self._native_user32 = None
