import sys
import signal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal, Qt
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from db import Store
from hotkeys import HotkeyListener
from ui import MainWindow

INSTANCE_KEY = "ebo-clipboard-single-instance"


class HotkeyBridge(QObject):
    triggered = pyqtSignal(str)


def build_bindings(store):
    bindings = {}
    toggle_kb = store.get_setting("toggle_window_keybind")
    if toggle_kb:
        bindings[toggle_kb] = "__toggle__"
    for m in store.list_all_messages():
        if m["keybind"]:
            bindings[m["keybind"]] = f"msg:{m['id']}"
    return bindings


def notify_running_instance():
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_KEY)
    if socket.waitForConnected(200):
        socket.write(b"show")
        socket.waitForBytesWritten(200)
        socket.disconnectFromServer()
        return True
    return False


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ebo clipboard")
    app.setApplicationDisplayName("ebo clipboard")

    if notify_running_instance():
        sys.exit(0)

    QLocalServer.removeServer(INSTANCE_KEY)
    instance_server = QLocalServer()
    instance_server.listen(INSTANCE_KEY)

    app.setQuitOnLastWindowClosed(False)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    store = Store()
    bridge = HotkeyBridge()
    listener = HotkeyListener(lambda action: bridge.triggered.emit(action))

    window = MainWindow(store, listener)

    def handle_new_connection():
        socket = instance_server.nextPendingConnection()
        window.show_and_raise()
        if socket:
            socket.disconnectFromServer()

    instance_server.newConnection.connect(handle_new_connection)

    def handle_action(action):
        if action == "__toggle__":
            window.toggle_visible()
        elif action.startswith("msg:"):
            msg_id = int(action.split(":", 1)[1])
            window.paste_message_by_id(msg_id)

    bridge.triggered.connect(handle_action)
    listener.start()
    listener.set_bindings(build_bindings(store))
    window.refresh_bindings_cb = lambda: listener.set_bindings(build_bindings(store))

    app.aboutToQuit.connect(listener.stop)

    window.show()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()