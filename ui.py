import time
import os
import sys

from PyQt6.QtWidgets import (
    QWidget, QMainWindow, QListWidget, QListWidgetItem, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QLabel, QDialog, QTextEdit, QMenu, QSystemTrayIcon,
    QFileDialog, QMessageBox, QAbstractItemView, QInputDialog,
    QFrame, QApplication, QListView, QToolTip, QSizePolicy, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import (
    Qt, QTimer, QSize, pyqtSignal, QEvent, QBuffer, QIODevice, QMimeData,
    QPoint, QRect, QPropertyAnimation, QEasingCurve, QByteArray, QAbstractAnimation,
    QSettings
)
from PyQt6.QtGui import QAction, QDrag, QIcon, QKeySequence, QImage, QPixmap, QBitmap, QRegion, QColor, QPainter, QFontDatabase
from PyQt6.QtSvg import QSvgRenderer
from sync import SyncManager, GitHubBackend, SyncError, load_token, save_token

import inputsim

ICON_PATH = os.path.join(
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
    "icon.png",
)
SEARCH_DEBOUNCE_MS = 180
# حجم ابتدائي قريب من مساحة النافذة الظاهرة في الصورة.
WINDOW_WIDTH_RATIO = 0.81
WINDOW_HEIGHT_RATIO = 0.95
WINDOW_MIN_WIDTH = 960
WINDOW_MIN_HEIGHT = 480
COMPACT_HEADER_WIDTH = 1150
WHEEL_STEP_PIXELS = 120
CARD_MIN_WIDTH = 280
CARD_HEIGHT = 190


def load_app_icon():
    if not os.path.isfile(ICON_PATH):
        raise FileNotFoundError(f"Application icon not found: {ICON_PATH}")
    icon = QIcon(ICON_PATH)
    if icon.isNull():
        raise RuntimeError(f"Unable to load application icon: {ICON_PATH}")
    return icon


ICONS = {
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "pencil": '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
}


def svg_pixmap(name, color, size=18, stroke=2):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
        f"{ICONS[name]}</svg>"
    )
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def make_icon(name, color, size=18):
    return QIcon(svg_pixmap(name, color, size))


def make_group_icon():
    icon = QIcon()
    normal = svg_pixmap("folder", "#8D99AE", 18)
    active = svg_pixmap("folder", "#7DD3FC", 18)
    icon.addPixmap(normal, QIcon.Mode.Normal)
    icon.addPixmap(active, QIcon.Mode.Selected)
    icon.addPixmap(active, QIcon.Mode.Active)
    return icon


class IconButton(QPushButton):
    def __init__(self, name, color, hover_color, size=18, parent=None):
        super().__init__(parent)
        self._normal = make_icon(name, color, size)
        self._hover = make_icon(name, hover_color, size)
        self.setIcon(self._normal)
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        self.setIcon(self._hover)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setIcon(self._normal)
        super().leaveEvent(event)


def load_app_fonts():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    fonts_dir = os.path.join(base, "fonts")
    if os.path.isdir(fonts_dir):
        for name in sorted(os.listdir(fonts_dir)):
            if name.lower().endswith((".ttf", ".otf")):
                QFontDatabase.addApplicationFont(os.path.join(fonts_dir, name))


def load_brand_pixmap(size):
    image = QImage(ICON_PATH)
    if image.isNull():
        return QPixmap()
    mask = QBitmap.fromImage(image.createAlphaMask())
    bounds = QRegion(mask).boundingRect()
    if not bounds.isEmpty():
        image = image.copy(bounds)
    return QPixmap.fromImage(image).scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


ARABIC_DIGITS_TRANSLATION = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_digits(text):
    return text.translate(ARABIC_DIGITS_TRANSLATION)


class ErrorStatusLabel(QLabel):
    def __init__(self, text=""):
        super().__init__(text)
        self._error_message = None

    def set_error(self, message):
        self._error_message = message
        super().setText("Error")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("اضغط لنسخ رسالة الخطأ")
        self.setStyleSheet("color: #ff7777; font-size: 13px;")

    def setText(self, text):
        self._error_message = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip("")
        self.setStyleSheet("")
        super().setText(text)

    def mousePressEvent(self, event):
        if self._error_message and event.button() == Qt.MouseButton.LeftButton:
            QApplication.clipboard().setText(self._error_message)
            QToolTip.showText(
                event.globalPosition().toPoint(),
                "تم نسخ رسالة الخطأ",
                self,
            )
            event.accept()
            return
        super().mousePressEvent(event)


def show_error_dialog(parent, title, message):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)

    error_label = ErrorStatusLabel()
    error_label.set_error(message)
    layout.addWidget(error_label)

    close_button = QPushButton("إغلاق")
    close_button.clicked.connect(dialog.accept)
    layout.addWidget(close_button)
    dialog.exec()


STYLE = """
QWidget { background: #0D1117; color: #E6EAF2; font-family: 'Cairo', 'Noto Sans Arabic', 'Segoe UI', Arial, sans-serif; font-size: 14px; }
QMainWindow { background: #0D1117; }
QPushButton { background: #181E2B; border: 1px solid #263047; border-radius: 10px; padding: 8px 12px; }
QPushButton:hover { background: #1F2738; border-color: #33405C; }
QPushButton#primary, QPushButton#addMessage { background: #38BDF8; border: 1px solid #38BDF8; border-radius: 12px; color: #06202F; font-size: 14px; font-weight: bold; min-height: 26px; padding: 8px 20px; }
QPushButton#primary:hover, QPushButton#addMessage:hover { background: #7DD3FC; border-color: #7DD3FC; }
QPushButton#addGroupButton { background: transparent; border: 1px dashed #2B4A63; border-radius: 10px; color: #7DD3FC; font-weight: bold; min-height: 26px; }
QPushButton#addGroupButton:hover { background: #0F2A3D; }
QPushButton#backupButton { background: #161C28; border: 1px solid #263047; border-radius: 12px; color: #A9B4C8; font-size: 14px; font-weight: bold; padding: 0px 16px; }
QPushButton#backupButton:hover { background: #1F2738; color: #E6EAF2; }
QPushButton#backupButton::menu-indicator { image: none; width: 0px; }
QLineEdit, QTextEdit { background: #161C28; border: 1px solid #263047; border-radius: 10px; padding: 8px 10px; }
QLineEdit:focus, QTextEdit:focus { border: 1px solid #38BDF8; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item:focus { outline: none; border: none; }
QListWidget#msgList { background: transparent; }
QListWidget#msgList::item { padding: 5px; margin: 0px; border: none; background: transparent; }
QListWidget#msgList::item:selected, QListWidget#msgList::item:hover { background: transparent; }
QListWidget#groupList::item { padding: 11px 14px; border-radius: 10px; margin: 2px 0; color: #B4BECF; }
QListWidget#groupList::item:selected { background: #12324A; border: 1px solid #1D4E6E; color: #FFFFFF; }
QListWidget#groupList::item:hover:!selected { background: #1B2336; color: #E6EAF2; }
QFrame#sidebar { background: #121722; border-left: 1px solid #1F2738; }
QFrame#sidebarHeader { background: transparent; border-bottom: 1px solid #1F2738; }
QLabel#brandLogo { background: transparent; }
QLabel#proBadge { background: #0F2A3D; border: 1px solid #1D4E6E; border-radius: 5px; color: #7DD3FC; font-size: 9px; font-weight: bold; padding: 1px 5px; }
QFrame#sidebarFooter { background: #0D1117; border: 1px solid #1F2738; border-radius: 12px; }
QFrame#localAvatar { background: #161C28; border: 1px solid #263047; border-radius: 10px; }
QLabel#localAvatarText { background: transparent; color: #38BDF8; font-size: 15px; font-weight: bold; }
QLabel#localStatus { background: transparent; color: #E6EAF2; font-size: 16px; font-weight: bold; }
QLabel#localStatusDetail { background: transparent; color: #8D99AE; font-size: 11px; }
QLabel#localStatusDetail[state="ok"] { color: #34D399; }
QLabel#localStatusDetail[state="syncing"] { color: #FBBF24; }
QLabel#localStatusDetail[state="error"] { color: #F87171; }
QPushButton#sidebarSettings { background: transparent; border: none; color: #8D99AE; font-size: 17px; padding: 7px; }
QPushButton#sidebarSettings:hover { background: #1F2738; color: #E6EAF2; }
QFrame#msgCard { background: #181E2B; border: 1px solid #263047; border-radius: 14px; }
QFrame#msgCard:hover { border-color: #38BDF8; }
QFrame#msgCard[dragging="true"] { background: #1B2740; border: 1px solid #38BDF8; }
QLabel#addGroupPlus { background: transparent; color: #7DD3FC; font-size: 20px; }
QLabel#addGroupText { background: transparent; color: #7DD3FC; font-size: 14px; font-weight: 600; }
QFrame#msgCard QLabel { background: transparent; }
QLabel#messageTitle { color: #E6EAF2; font-size: 16px; font-weight: bold; }
QLabel#messageContent { background: transparent; color: #A9B4C8; font-size: 14px; }
QLabel#groupBadge { background: #161C28; border: 1px solid #263047; border-radius: 6px; color: #8D99AE; padding: 2px 7px; font-size: 11px; }
QLabel#kbtag { background: #0F2A3D; border-radius: 6px; color: #7DD3FC; padding: 3px 8px; font-size: 12px; font-weight: bold; }
QLabel#sectionheading { color: #8D99AE; font-size: 12px; font-weight: bold; padding: 4px 2px; }
QLabel#appTitle { background: transparent; color: #E6EAF2; font-size: 18px; font-weight: bold; }
QLabel#sidebarSubtitle { background: transparent; color: #8D99AE; font-size: 11px; }
QLabel#pageTitle { color: #E6EAF2; font-size: 26px; font-weight: bold; }
QLabel#pageSubtitle { color: #8D99AE; font-size: 13px; }
QLabel#messageCount { background: #161C28; border: 1px solid #263047; border-radius: 8px; color: #A9B4C8; padding: 3px 9px; font-size: 12px; }
QFrame#emptyState { background: transparent; }
QLabel#emptyIcon { background: #181E2B; border: 1px solid #263047; border-radius: 16px; color: #64748b; font-size: 30px; }
QLabel#emptyTitle { color: #E6EAF2; font-size: 18px; font-weight: bold; }
QLabel#emptyDescription { color: #8D99AE; font-size: 12px; }
QFrame#cardDivider { background: #263047; max-height: 1px; }
QScrollBar:vertical { background: transparent; width: 10px; border: none; margin: 0px; padding: 0px; border-radius: 5px; }
QScrollBar::handle:vertical { background: #2B3A55; min-height: 44px; border: none; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #38BDF8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; border: none; height: 0px; }
QFrame#searchBox { background: #161C28; border: 1px solid #263047; border-radius: 12px; }
QFrame#searchBox QLineEdit { background: transparent; border: none; padding: 8px 4px; }
QLabel#searchIcon { color: #8D99AE; padding-left: 4px; background: transparent; }
QPushButton#copyMessage { background: #0F2A3D; border: 1px solid #1D4E6E; color: #7DD3FC; font-weight: bold; padding: 4px 10px; }
QPushButton#copyMessage:hover { background: #12324A; border-color: #38BDF8; }
QPushButton#cardAction, QPushButton#deleteAction { background: transparent; border: none; border-radius: 8px; padding: 0px; }
QPushButton#cardAction:hover, QPushButton#deleteAction:hover { background: #1F2738; }
QPushButton#allMessagesButton, QPushButton#favoritesButton { background: #12324A; border: 1px solid #1D4E6E; color: #FFFFFF; font-weight: bold; padding: 10px 14px; text-align: right; }
QPushButton#allMessagesButton:!checked, QPushButton#favoritesButton:!checked { background: transparent; border-color: transparent; color: #B4BECF; font-weight: normal; }
QPushButton#allMessagesButton:!checked:hover, QPushButton#favoritesButton:!checked:hover { background: #1B2336; color: #E6EAF2; }
QPushButton#starButton { background: transparent; border: none; color: #8D99AE; font-size: 18px; padding: 0; }
QPushButton#starButton:hover { background: transparent; color: #FBBF24; }
QPushButton#starButton:checked { color: #FBBF24; }
"""


class MessageListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.message_reorder_handler = None
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(48)
        self._wheel_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._wheel_animation.setDuration(220)
        self._wheel_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def stop_wheel_animation(self):
        self._wheel_animation.stop()

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        pixel = event.pixelDelta().y()
        angle = event.angleDelta().y()
        if bar.maximum() <= bar.minimum() or (pixel == 0 and angle == 0):
            super().wheelEvent(event)
            return
        if pixel:
            self._wheel_animation.stop()
            bar.setValue(bar.value() - pixel)
        else:
            running = self._wheel_animation.state() == QAbstractAnimation.State.Running
            base = self._wheel_animation.endValue() if running else bar.value()
            target = int(base - angle / 120 * WHEEL_STEP_PIXELS)
            target = max(bar.minimum(), min(bar.maximum(), target))
            self._wheel_animation.stop()
            self._wheel_animation.setStartValue(bar.value())
            self._wheel_animation.setEndValue(target)
            self._wheel_animation.start()
        event.accept()

    def mimeData(self, items):
        mime = super().mimeData(items)
        if items:
            message_id = items[0].data(Qt.ItemDataRole.UserRole)
            mime.setData("application/x-ebo-message-id", str(message_id).encode())
        return mime

    def dropEvent(self, event):
        if event.source() is self:
            super().dropEvent(event)
        elif (
            event.mimeData().hasFormat("application/x-ebo-message-id")
            and self.message_reorder_handler
        ):
            message_id = int(
                bytes(event.mimeData().data("application/x-ebo-message-id")).decode()
            )
            target_item = self.itemAt(event.position().toPoint())
            target_id = (
                target_item.data(Qt.ItemDataRole.UserRole) if target_item else None
            )
            self.message_reorder_handler(message_id, target_id)
            event.acceptProposedAction()
        else:
            event.ignore()


class GroupListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.message_drop_handler = None
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat("application/x-ebo-message-id"):
            target_item = self.itemAt(event.position().toPoint())
            if target_item and self.message_drop_handler:
                group_id = target_item.data(Qt.ItemDataRole.UserRole)
                message_id = int(bytes(mime.data("application/x-ebo-message-id")).decode())
                self.message_drop_handler(message_id, group_id)
            event.acceptProposedAction()
            return
        if event.source() is self:
            super().dropEvent(event)
        else:
            event.ignore()


class SearchBox(QFrame):
    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setObjectName("searchBox")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setObjectName("searchIcon")
        icon.setPixmap(svg_pixmap("search", "#8D99AE", 18))
        icon.setFixedWidth(20)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        self.input.setFrame(False)
        layout.addWidget(icon)
        layout.addWidget(self.input, 1)

    def text(self):
        return self.input.text()

    def clear(self):
        self.input.clear()

    def cursorPosition(self):
        return self.input.cursorPosition()

    def blockSignals(self, block):
        return self.input.blockSignals(block)

    def setText(self, text):
        self.input.setText(text)

    def setCursorPosition(self, position):
        self.input.setCursorPosition(position)

    @property
    def textChanged(self):
        return self.input.textChanged


class MessageCard(QFrame):
    copy_requested = pyqtSignal(int)
    edit_requested = pyqtSignal(int)
    delete_requested = pyqtSignal(int)
    star_toggled = pyqtSignal(int, bool)
    clicked = pyqtSignal(int)
    double_clicked = pyqtSignal(int)
    drag_started = pyqtSignal(int, QPoint)
    drag_moved = pyqtSignal(int, QPoint)
    drag_finished = pyqtSignal(int, QPoint)

    def __init__(self, message, group_name, parent=None, draggable=False):
        super().__init__(parent)
        self.message_id = message["id"]
        self.draggable = draggable
        self._drag_start_position = None
        self._dragging = False
        self.setObjectName("msgCard")
        self.setFixedSize(CARD_MIN_WIDTH + 52, CARD_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel(message["name"])
        title.setObjectName("messageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        title.setWordWrap(True)
        title.setMaximumHeight(46)
        title_row.addWidget(title, 1)
        star_button = QPushButton("★" if message.get("starred") else "☆")
        star_button.setObjectName("starButton")
        star_button.setCheckable(True)
        star_button.setChecked(bool(message.get("starred")))
        star_button.setToolTip("تمييز بنجمة")
        star_button.setFixedSize(30, 30)
        star_button.setCursor(Qt.CursorShape.PointingHandCursor)

        def on_star(checked, button=star_button):
            button.setText("★" if checked else "☆")
            self.star_toggled.emit(self.message_id, checked)

        star_button.toggled.connect(on_star)
        title_row.addWidget(star_button)
        drag_handle = QLabel("⠿")
        drag_handle.setToolTip("اسحب لترتيب الرسالة أو نقلها")
        drag_handle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_row.addWidget(drag_handle)
        layout.addLayout(title_row)

        content = QLabel(message["content"] or ("[صورة]" if message["has_image"] else ""))
        content.setObjectName("messageContent")
        content.setWordWrap(True)
        content.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        layout.addWidget(content, 1)

        footer = QHBoxLayout()
        footer.setSpacing(2)
        edit_button = IconButton("pencil", "#8D99AE", "#38BDF8")
        edit_button.setObjectName("cardAction")
        edit_button.setToolTip("تعديل الرسالة")
        edit_button.setFixedSize(32, 32)
        edit_button.clicked.connect(lambda: self.edit_requested.emit(self.message_id))
        delete_button = IconButton("trash", "#8D99AE", "#F87171")
        delete_button.setObjectName("deleteAction")
        delete_button.setToolTip("حذف الرسالة")
        delete_button.setFixedSize(32, 32)
        delete_button.clicked.connect(lambda: self.delete_requested.emit(self.message_id))
        footer.addWidget(edit_button)
        footer.addWidget(delete_button)
        keybind = message.get("keybind")
        if keybind:
            footer.addSpacing(6)
            keybind_label = QLabel(keybind)
            keybind_label.setObjectName("kbtag")
            footer.addWidget(keybind_label)
        footer.addStretch(1)
        copy_button = QPushButton("نسخ ولصق")
        copy_button.setObjectName("copyMessage")
        copy_button.clicked.connect(lambda: self.copy_requested.emit(self.message_id))
        footer.addWidget(copy_button)
        layout.addLayout(footer)
        if group_name:
            self.setToolTip(f"المجموعة: {group_name}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_position = event.position().toPoint()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_position is None or not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        if not self._dragging:
            moved = (event.position().toPoint() - self._drag_start_position).manhattanLength()
            if not self.draggable or moved < QApplication.startDragDistance():
                return
            self._dragging = True
            self.drag_started.emit(self.message_id, self._drag_start_position)
        self.drag_moved.emit(self.message_id, event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start_position is not None:
            was_dragging = self._dragging
            self._drag_start_position = None
            self._dragging = False
            if was_dragging:
                self.drag_finished.emit(self.message_id, event.globalPosition().toPoint())
            elif self.rect().contains(event.position().toPoint()):
                self.clicked.emit(self.message_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.message_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class KeybindCaptureDialog(QDialog):
    F_KEYS = {getattr(Qt.Key, f"Key_F{i}"): f"f{i}" for i in range(1, 25)}
    NAME_OVERRIDES = {
        Qt.Key.Key_Return: "enter",
        Qt.Key.Key_Enter: "enter",
        Qt.Key.Key_Backspace: "backspace",
        Qt.Key.Key_Tab: "tab",
        Qt.Key.Key_Escape: "esc",
        Qt.Key.Key_Space: "space",
        Qt.Key.Key_Delete: "delete",
        Qt.Key.Key_Up: "up",
        Qt.Key.Key_Down: "down",
        Qt.Key.Key_Left: "left",
        Qt.Key.Key_Right: "right",
        Qt.Key.Key_Home: "home",
        Qt.Key.Key_End: "end",
        Qt.Key.Key_PageUp: "page up",
        Qt.Key.Key_PageDown: "page down",
        Qt.Key.Key_Insert: "insert",
    }
    MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}
    DIGIT_OR_OPERATOR_KEYS = set(range(Qt.Key.Key_0.value, Qt.Key.Key_9.value + 1)) | {
        Qt.Key.Key_Plus.value, Qt.Key.Key_Minus.value, Qt.Key.Key_Asterisk.value,
        Qt.Key.Key_Slash.value, Qt.Key.Key_Period.value,
    }

    def __init__(self, parent=None, listener=None):
        super().__init__(parent)
        self.setWindowTitle("Set Keybind")
        self.combo = ""
        self.listener = listener
        if self.listener:
            self.listener.pause()
        layout = QVBoxLayout(self)
        self.label = QLabel("اضغط تركيبة المفاتيح اللي عايزها، وسيبها")
        layout.addWidget(self.label)
        btns = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(clear)
        btns.addWidget(cancel)
        layout.addLayout(btns)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _clear(self):
        self.combo = ""
        self.label.setText("اضغط تركيبة المفاتيح اللي عايزها، وسيبها")

    def _key_name(self, key):
        if key in self.NAME_OVERRIDES:
            return self.NAME_OVERRIDES[key]
        if key in self.F_KEYS:
            return self.F_KEYS[key]
        if Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value:
            return chr(key).lower()
        return None

    def _modifier_parts(self, mods):
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("windows")
        return parts

    def event(self, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key in self.MODIFIER_KEYS:
            return
        parts = self._modifier_parts(event.modifiers())
        if key in self.DIGIT_OR_OPERATOR_KEYS:
            scan = event.nativeScanCode()
            is_keypad = bool(event.modifiers() & Qt.KeyboardModifier.KeypadModifier)
            name = f"scan:{scan}"
            suffix = " (نمباد)" if is_keypad else " (الصف العلوي)"
        else:
            name = self._key_name(key)
            suffix = ""
        if not name:
            return
        parts.append(name)
        self.combo = "+".join(parts)
        self.label.setText(self.combo + suffix)

    def keyReleaseEvent(self, event):
        if self.combo:
            self.accept()

    def done(self, result):
        if self.listener:
            self.listener.resume()
            self.listener = None
        super().done(result)


class MessageEditorDialog(QDialog):
    def __init__(self, parent=None, name="", content="", keybind=None, image=None):
        super().__init__(parent)
        self.setWindowTitle("Message")
        self.resize(460, 440)
        self.keybind = keybind
        self.image = image
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit(name)
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("Content"))
        self.content_edit = QTextEdit()
        self.content_edit.setPlainText(content)
        self.content_edit.setMinimumHeight(160)
        layout.addWidget(self.content_edit)

        image_row = QHBoxLayout()
        self.image_status = QLabel()
        self.image_preview = QLabel()
        self.image_preview.setFixedSize(72, 56)
        add_image_btn = QPushButton("أضف صورة من الحافظة")
        add_image_btn.clicked.connect(self._capture_clipboard_image)
        remove_image_btn = QPushButton("إزالة الصورة")
        remove_image_btn.clicked.connect(self._remove_image)
        image_row.addWidget(self.image_preview)
        image_row.addWidget(self.image_status, 1)
        image_row.addWidget(add_image_btn)
        image_row.addWidget(remove_image_btn)
        layout.addLayout(image_row)
        self._update_image_preview()

        kb_row = QHBoxLayout()
        self.kb_label = QLabel(keybind or "No keybind")
        kb_btn = QPushButton("Set Keybind")
        kb_btn.clicked.connect(self._set_keybind)
        kb_row.addWidget(self.kb_label, 1)
        kb_row.addWidget(kb_btn)
        layout.addLayout(kb_row)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)

    def _set_keybind(self):
        dlg = KeybindCaptureDialog(self, listener=self.parent().listener)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.combo:
            self.keybind = dlg.combo
            self.kb_label.setText(dlg.combo)

    def _capture_clipboard_image(self):
        image = QApplication.clipboard().image()
        if image.isNull():
            QMessageBox.information(self, "لا توجد صورة", "انسخ صورة إلى الحافظة أولًا.")
            return
        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            show_error_dialog(self, "Image capture failed", "Could not open a buffer to save the clipboard image")
            return
        if not image.save(buffer, "PNG"):
            show_error_dialog(self, "Image capture failed", "Could not encode the clipboard image")
            return
        self.image = bytes(buffer.data())
        self._update_image_preview()

    def _remove_image(self):
        self.image = None
        self._update_image_preview()

    def _update_image_preview(self):
        image = QImage.fromData(self.image) if self.image else QImage()
        if image.isNull():
            self.image_preview.clear()
            self.image_status.setText("لا توجد صورة")
            return
        self.image_preview.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.image_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.image_status.setText("تم حفظ الصورة")

    def values(self):
        return self.name_edit.text().strip(), self.content_edit.toPlainText(), self.keybind, self.image


def relative_time_ar(ms):
    seconds = max(0, (time.time() * 1000 - ms) / 1000)
    if seconds < 45:
        return "الآن"
    if seconds < 3600:
        return f"منذ {max(1, int(seconds // 60))} دقيقة"
    if seconds < 86400:
        return f"منذ {int(seconds // 3600)} ساعة"
    return f"منذ {int(seconds // 86400)} يوم"


class SettingsDialog(QDialog):
    def __init__(self, parent, store, on_change, sync=None):
        super().__init__(parent)
        self.setWindowTitle("الإعدادات")
        self.resize(460, 380)
        self.store = store
        self.on_change = on_change
        self.sync = sync
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        row = QHBoxLayout()
        row.addWidget(QLabel("اختصار إظهار/إخفاء النافذة"))
        self.toggle_btn = QPushButton(store.get_setting("toggle_window_keybind") or "غير محدد")
        self.toggle_btn.clicked.connect(self._set_toggle)
        row.addWidget(self.toggle_btn)
        layout.addLayout(row)

        layout.addSpacing(6)
        heading = QLabel("المزامنة مع GitHub (مستودع خاص)")
        heading.setObjectName("sectionheading")
        layout.addWidget(heading)
        hint = QLabel("بياناتك بتتخزن في مستودع Private عندك، وأي تعديل بيظهر على الجهاز التاني.")
        hint.setObjectName("pageSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.repo_edit = QLineEdit(store.get_setting("sync_repo") or "")
        self.repo_edit.setPlaceholderText("username/repo-name")
        self.repo_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        layout.addWidget(QLabel("اسم المستودع"))
        layout.addWidget(self.repo_edit)

        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        has_token = bool(load_token(store))
        self.token_edit.setPlaceholderText(
            "محفوظ — اتركه فاضي للإبقاء عليه" if has_token else "github_pat_... (صلاحية Contents: Read and write)"
        )
        layout.addWidget(QLabel("التوكن"))
        layout.addWidget(self.token_edit)

        buttons = QHBoxLayout()
        self.connect_btn = QPushButton("حفظ واختبار الاتصال")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(self._connect)
        self.sync_btn = QPushButton("مزامنة الآن")
        self.sync_btn.clicked.connect(self._sync_now)
        buttons.addWidget(self.connect_btn)
        buttons.addWidget(self.sync_btn)
        layout.addLayout(buttons)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        close_btn = QPushButton("إغلاق")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _say(self, text, ok=True):
        self.status_label.setStyleSheet("color: %s;" % ("#34D399" if ok else "#F87171"))
        self.status_label.setText(text)

    def _connect(self):
        repo = self.repo_edit.text().strip()
        if repo.count("/") != 1 or not all(repo.split("/")):
            self._say("اكتب اسم المستودع بصيغة username/repo", ok=False)
            return
        token = self.token_edit.text().strip() or load_token(self.store)
        if not token:
            self._say("ضيف التوكن الأول", ok=False)
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = GitHubBackend(repo, token, timeout=15).check()
        except SyncError as error:
            self._say(str(error), ok=False)
            return
        finally:
            QApplication.restoreOverrideCursor()
        if not info["private"]:
            self._say("المستودع ده عام (Public)! حوّله لـ Private الأول علشان بياناتك ما تتشافش.", ok=False)
            return
        if not info["can_push"]:
            self._say("التوكن للقراءة بس، محتاج صلاحية Contents: Read and write.", ok=False)
            return
        self.store.set_setting("sync_repo", repo)
        message = f"تم الاتصال بـ {info['name']} ✓"
        if self.token_edit.text().strip():
            if not save_token(self.store, token):
                message += "\nمكتبة keyring مش متثبتة، فالتوكن اتحفظ محليًا. الأفضل: pip install keyring ثم أدخله تاني."
            self.token_edit.clear()
            self.token_edit.setPlaceholderText("محفوظ — اتركه فاضي للإبقاء عليه")
        self._say(message)
        self.on_change()
        if self.sync is not None:
            self.sync.sync_now("setup")

    def _sync_now(self):
        if self.sync is None or not self.sync.configured():
            self._say("اضبط المستودع والتوكن الأول", ok=False)
            return
        self.sync.sync_now("manual")
        self._say("بدأت المزامنة، تابع الحالة في أسفل القائمة الجانبية.")

    def _set_toggle(self):
        dialog = KeybindCaptureDialog(self, listener=self.parent().listener)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.combo:
            self.parent()._resolve_keybind_conflict(
                dialog.combo, exclude_setting_key="toggle_window_keybind"
            )
            self.store.set_setting("toggle_window_keybind", dialog.combo)
            self.toggle_btn.setText(dialog.combo)
            self.on_change()


class MainWindow(QMainWindow):


    def _do_paste_hidden(self, content):
        was_visible = self.isVisible()
        self.hide()
        delay = 180 if was_visible else 0
        QTimer.singleShot(delay, lambda: self._do_paste(content))


    def _on_search_changed(self, text):
        converted = normalize_digits(text)
        if converted != text:
            cursor_pos = self.search_input.cursorPosition()
            self.search_input.blockSignals(True)
            self.search_input.setText(converted)
            self.search_input.blockSignals(False)
            self.search_input.setCursorPosition(cursor_pos)
        self._main_search_timer.start()


    def _build_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(load_app_icon())
        menu = QMenu()
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show_and_raise)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle_visible() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self.tray.show()

    def _quit(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

    def toggle_visible(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_and_raise()

    def show_and_raise(self):
        if not self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.show()
        self.raise_()
        self.activateWindow()


    def _group_context_menu(self, pos):
        item = self.group_list.itemAt(pos)
        if not item:
            return
        gid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu()
        rename = menu.addAction("Rename")
        delete = menu.addAction("Delete")
        action = menu.exec(self.group_list.mapToGlobal(pos))
        if action == rename:
            self._rename_group(item, gid)
        elif action == delete:
            self._delete_group(gid)


    def _delete_group(self, gid):
        if QMessageBox.question(self, "Delete group", "Delete this group and its messages?") == QMessageBox.StandardButton.Yes:
            self.store.delete_group(gid)
            if self.current_group_id == gid:
                self.current_group_id = None
            self._refresh_groups()
            self._refresh_messages()
            self.refresh_bindings_cb()

    def _groups_reordered(self, *args):
        ids = [self.group_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.group_list.count())]
        self.store.reorder_groups(ids)


    def _message_context_menu(self, pos):
        item = self.msg_list.itemAt(pos)
        if not item:
            return
        mid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu()
        edit = menu.addAction("Edit")
        delete = menu.addAction("Delete")
        action = menu.exec(self.msg_list.mapToGlobal(pos))
        if action == edit:
            self._message_edit(item)
        elif action == delete:
            if QMessageBox.question(self, "Delete message", "Delete this message?") == QMessageBox.StandardButton.Yes:
                self.store.delete_message(mid)
                self._refresh_groups()
                self._refresh_messages()
                self.refresh_bindings_cb()


    def _handle_message_dropped_on_group(self, message_id, group_id):
        if group_id == self.current_group_id:
            return
        self.store.move_message(message_id, group_id)
        self._refresh_groups()
        self._refresh_messages_animated()

    def _message_clicked(self, item):
        self._message_clicked_id(item.data(Qt.ItemDataRole.UserRole))

    def _message_clicked_id(self, mid):
        msg = next((m for m in self.store.list_all_messages() if m["id"] == mid), None)
        if msg and msg["flagged"]:
            self.store.set_flagged(mid, 0)
            self._refresh_messages()
        self.paste_message_by_id(mid)

    def _resolve_keybind_conflict(self, keybind, exclude_message_id=None, exclude_setting_key=None):
        if not keybind:
            return
        for m in self.store.list_all_messages():
            if m["id"] == exclude_message_id:
                continue
            if m["keybind"] == keybind:
                self.store.set_keybind(m["id"], None)
                self.store.set_flagged(m["id"], 1)
        setting_labels = {
            "toggle_window_keybind": "فتح/إخفاء النافذة",
            "backspace_keybind": "Backspace",
            "numpad_enter_keybind": "Numpad Enter",
            "next_field_keybind": "الحقل التالي",
        }
        for key, label in setting_labels.items():
            if key == exclude_setting_key:
                continue
            if self.store.get_setting(key) == keybind:
                self.store.set_setting(key, "")
                QMessageBox.information(self, "تعارض اختصار", f'الاختصار ده كان متسجل لـ"{label}" في الإعدادات، اتشال منه.')

    def paste_message_by_id(self, mid):
        all_msgs = self.store.list_all_messages()
        msg = next((m for m in all_msgs if m["id"] == mid), None)
        if not msg:
            return
        content = self.store.get_message_image(mid) if msg["has_image"] else msg["content"]
        self._do_paste_hidden(content)

    def _do_paste(self, content):
        try:
            if isinstance(content, bytes):
                inputsim.paste_image(content)
            else:
                inputsim.paste_text(content)
        except Exception as e:
            show_error_dialog(self, "Paste failed", str(e))

    def simulate_backspace(self):
        try:
            inputsim.simulate_backspace()
        except Exception as e:
            show_error_dialog(self, "Backspace failed", str(e))

    def simulate_numpad_enter(self):
        try:
            inputsim.simulate_numpad_enter()
        except Exception as e:
            show_error_dialog(self, "Numpad Enter failed", str(e))

    def _open_settings(self):
        dlg = SettingsDialog(self, self.store, self._on_settings_changed, self.sync)
        dlg.exec()

    def _on_settings_changed(self):
        self.refresh_bindings_cb()
        self._refresh_groups()
        self._update_sync_footer()

    # ---------- المزامنة ----------
    def _sync_clicked(self):
        if not self.sync.configured():
            self._open_settings()
        else:
            self.sync.sync_now("manual")

    def _update_sync_footer(self):
        state = self.sync.state
        message = self.sync.message
        repo = (self.store.get_setting("sync_repo") or "").strip()
        github_username = repo.split("/", 1)[0] if "/" in repo else repo
        configured = self.sync.configured()
        self.sync_avatar_label.setText(github_username[:1].upper() if configured and github_username else "ع")
        if not configured:
            title, detail, tone = "حساب محلي", "البيانات محفوظة على هذا الجهاز — اضغط ⟳ لتفعيل المزامنة", "off"
        elif state == "syncing":
            title, detail, tone = github_username, "جاري المزامنة...", "syncing"
        elif state == "error":
            title, detail, tone = github_username, "فشلت المزامنة", "error"
        else:
            last = self.sync.last_sync_ms()
            detail = f"آخر مزامنة: {relative_time_ar(last)}" if last else "جاهزة للمزامنة"
            title, tone = github_username, "ok"
        self.sync_title_label.setText(title)
        self.sync_detail_label.setText(detail)
        self.sync_detail_label.setToolTip(message if state == "error" else "")
        self.sync_detail_label.setProperty("state", tone)
        self.sync_detail_label.style().unpolish(self.sync_detail_label)
        self.sync_detail_label.style().polish(self.sync_detail_label)
        self.sync_button.setEnabled(state != "syncing")

    def _on_remote_data(self):
        if self._drag is not None:
            QTimer.singleShot(800, self._on_remote_data)
            return
        self._refresh_groups()
        self._refresh_messages()
        self.refresh_bindings_cb()
        self._refresh_messages()

    def __init__(self, store, listener):
        super().__init__()
        load_app_fonts()
        self.store = store
        self.listener = listener
        self.refresh_bindings_cb = lambda: None
        self.current_group_id = None
        self._message_rows = []
        self._message_rows_group_id = None
        self._card_animations = {}
        self._drag = None
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(25)
        self._drag_timer.timeout.connect(self._drag_autoscroll)
        self._main_search_timer = QTimer(self)
        self._main_search_timer.setSingleShot(True)
        self._main_search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._main_search_timer.timeout.connect(self._apply_search)
        self._window_settings = QSettings("ClipMaster", "ClipMaster")
        self._window_size_save_timer = QTimer(self)
        self._window_size_save_timer.setSingleShot(True)
        self._window_size_save_timer.setInterval(300)
        self._window_size_save_timer.timeout.connect(self._save_window_size)

        self.setWindowTitle("ebo clipboard")
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1536, 864)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        saved_size = self._window_settings.value("window/size")
        if isinstance(saved_size, QSize) and saved_size.isValid():
            self.resize(saved_size)
        else:
            self.resize(
                max(WINDOW_MIN_WIDTH, int(available.width() * WINDOW_WIDTH_RATIO)),
                max(WINDOW_MIN_HEIGHT, int(available.height() * WINDOW_HEIGHT_RATIO)),
            )
        self.move(available.center() - self.rect().center())
        self.setStyleSheet(STYLE)
        self.setWindowIcon(load_app_icon())

        central = QWidget()
        central.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(270)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 0, 12, 12)
        sidebar_layout.setSpacing(12)

        sidebar_header = QFrame()
        sidebar_header.setObjectName("sidebarHeader")
        brand_layout = QHBoxLayout(sidebar_header)
        brand_layout.setContentsMargins(0, 14, 0, 14)
        brand_layout.setSpacing(10)

        brand_icon = QLabel()
        brand_icon.setObjectName("brandLogo")
        brand_icon.setPixmap(load_brand_pixmap(48))
        brand_icon.setFixedSize(48, 48)
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.addWidget(brand_icon)

        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(1)
        brand_title_row = QHBoxLayout()
        brand_title_row.setSpacing(6)
        brand = QLabel("ebo clipboard")
        brand.setObjectName("appTitle")
        pro_badge = QLabel("PRO")
        pro_badge.setObjectName("proBadge")
        brand_title_row.addWidget(brand)
        brand_title_row.addWidget(pro_badge)
        brand_title_row.addStretch(1)
        brand_text.addLayout(brand_title_row)
        brand_note = QLabel("مدير الحافظة والرسائل")
        brand_note.setObjectName("sidebarSubtitle")
        brand_text.addWidget(brand_note)
        brand_layout.addLayout(brand_text, 1)
        sidebar_layout.addWidget(sidebar_header)

        groups_heading = QLabel("المجموعات (اسحب للترتيب)")
        groups_heading.setObjectName("sectionheading")
        groups_heading.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sidebar_layout.addWidget(groups_heading)

        self.group_list = GroupListWidget()
        self.group_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.group_list.verticalScrollBar().setSingleStep(36)
        self.group_list.setObjectName("groupList")
        self.group_list.setIconSize(QSize(18, 18))
        self.group_list.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.group_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.group_list.setStyleSheet("QListWidget { direction: rtl; } QScrollBar:vertical { subcontrol-position: top right; }")
        self.group_list.message_drop_handler = self._handle_message_dropped_on_group
        self.group_list.currentItemChanged.connect(self._group_selected)
        self.group_list.model().rowsMoved.connect(self._groups_reordered)
        self.group_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.group_list.customContextMenuRequested.connect(self._group_context_menu)
        sidebar_layout.addWidget(self.group_list, 1)

        self.add_group_button = QPushButton()
        self.add_group_button.setObjectName("addGroupButton")
        self.add_group_button.setFixedHeight(46)
        self.add_group_button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_group_layout = QHBoxLayout(self.add_group_button)
        add_group_layout.setContentsMargins(0, 0, 0, 0)
        add_group_layout.setSpacing(8)
        add_group_plus = QLabel()
        add_group_plus.setObjectName("addGroupPlus")
        add_group_plus.setPixmap(svg_pixmap("plus", "#7DD3FC", 18))
        add_group_text = QLabel("مجموعة جديدة")
        add_group_text.setObjectName("addGroupText")
        for label in (add_group_plus, add_group_text):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        add_group_layout.addStretch(1)
        add_group_layout.addWidget(add_group_plus)
        add_group_layout.addWidget(add_group_text)
        add_group_layout.addStretch(1)
        self.add_group_button.clicked.connect(self._add_group)
        sidebar_layout.addWidget(self.add_group_button)

        sidebar_footer = QFrame()
        sidebar_footer.setObjectName("sidebarFooter")
        footer_layout = QHBoxLayout(sidebar_footer)
        footer_layout.setContentsMargins(10, 9, 8, 9)
        footer_layout.setSpacing(9)
        avatar = QFrame()
        avatar.setObjectName("localAvatar")
        avatar.setFixedSize(36, 36)
        avatar_layout = QVBoxLayout(avatar)
        avatar_layout.setContentsMargins(0, 0, 0, 0)
        avatar_text = QLabel("ع")
        avatar_text.setObjectName("localAvatarText")
        avatar_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_layout.addWidget(avatar_text)
        footer_layout.addWidget(avatar)
        self.sync_avatar_label = avatar_text

        local_status_layout = QVBoxLayout()
        local_status_layout.setContentsMargins(0, 0, 0, 0)
        local_status_layout.setSpacing(1)
        self.sync_title_label = QLabel("حساب محلي")
        self.sync_title_label.setObjectName("localStatus")
        self.sync_title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sync_title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.sync_detail_label = QLabel("البيانات محفوظة على هذا الجهاز")
        self.sync_detail_label.setObjectName("localStatusDetail")
        self.sync_detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sync_detail_label.setWordWrap(True)
        self.sync_detail_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        local_status_layout.addWidget(self.sync_title_label)
        local_status_layout.addWidget(self.sync_detail_label)
        footer_layout.addLayout(local_status_layout, 1)

        self.sync_button = IconButton("refresh", "#8D99AE", "#38BDF8", size=17)
        self.sync_button.setObjectName("sidebarSettings")
        self.sync_button.setFixedSize(34, 34)
        self.sync_button.setToolTip("مزامنة الآن")
        self.sync_button.clicked.connect(self._sync_clicked)
        footer_layout.addWidget(self.sync_button)

        settings_button = QPushButton("⚙")
        settings_button.setObjectName("sidebarSettings")
        settings_button.setFixedSize(34, 34)
        settings_button.setToolTip("الإعدادات")
        settings_button.clicked.connect(self._open_settings)
        footer_layout.addWidget(settings_button)
        sidebar_layout.addWidget(sidebar_footer)

        main_panel = QWidget()
        main_panel.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(28, 24, 20, 18)
        main_layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(12)
        page_heading = QVBoxLayout()
        page_heading.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.messages_heading = QLabel("جميع الرسائل")
        self.messages_heading.setObjectName("pageTitle")
        self.messages_heading.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.message_count_label = QLabel("0 رسالة")
        self.message_count_label.setObjectName("messageCount")
        title_row.addWidget(self.messages_heading)
        title_row.addWidget(self.message_count_label)
        title_row.addStretch(1)
        page_heading.addLayout(title_row)
        self.messages_subtitle = QLabel("يمكنك سحب الرسائل لترتيبها أو نقلها لمجموعة أخرى")
        self.messages_subtitle.setObjectName("pageSubtitle")
        self.messages_subtitle.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        subtitle_row = QHBoxLayout()
        subtitle_row.addWidget(self.messages_subtitle)
        subtitle_row.addStretch(1)
        page_heading.addLayout(subtitle_row)
        header.addLayout(page_heading, 2)

        self.search_input = SearchBox("ابحث في الرسائل...")
        self.search_input.setFixedHeight(44)
        self.search_input.setMinimumWidth(200)
        self.search_input.setMaximumWidth(300)
        self.search_input.setToolTip("Ctrl + F")
        self.search_input.textChanged.connect(self._on_search_changed)
        header.addWidget(self.search_input, 1)

        backup_menu = QMenu(self)
        export_action = backup_menu.addAction("تصدير نسخة احتياطية (.zdb)")
        export_action.triggered.connect(self._export_messages)
        import_action = backup_menu.addAction("استيراد نسخة احتياطية (.zdb)")
        import_action.triggered.connect(self._import_messages)
        backup_button = QPushButton("  نسخة احتياطية")
        self.backup_button = backup_button
        backup_button.setToolTip("نسخة احتياطية")
        backup_button.setObjectName("backupButton")
        backup_button.setIcon(make_icon("database", "#A9B4C8", 18))
        backup_button.setIconSize(QSize(18, 18))
        backup_button.setFixedHeight(44)
        backup_button.setMenu(backup_menu)
        header.addWidget(backup_button)
        add_message_button = QPushButton("  إضافة رسالة")
        add_message_button.setObjectName("addMessage")
        add_message_button.setIcon(make_icon("plus", "#06202F", 18))
        add_message_button.setIconSize(QSize(18, 18))
        add_message_button.setFixedHeight(44)
        add_message_button.clicked.connect(self._add_message)
        header.addWidget(add_message_button)
        main_layout.addLayout(header)

        self.empty_state = QFrame()
        self.empty_state.setObjectName("emptyState")
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(0, 28, 0, 28)
        empty_layout.setSpacing(8)
        empty_layout.addStretch(1)
        empty_icon = QLabel("▤")
        empty_icon.setObjectName("emptyIcon")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon.setFixedSize(68, 68)
        empty_layout.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_title = QLabel("لا توجد رسائل في هذه المجموعة")
        empty_title.setObjectName("emptyTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_description = QLabel("أضف رسالة جديدة لتظهر هنا")
        empty_description.setObjectName("emptyDescription")
        empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_description)
        empty_add_button = QPushButton("＋  إضافة رسالة")
        empty_add_button.setObjectName("addMessage")
        empty_add_button.clicked.connect(self._add_message)
        empty_layout.addWidget(empty_add_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)
        main_layout.addWidget(self.empty_state, 1)

        self.msg_list = MessageListWidget()
        self.msg_list.setObjectName("msgList")
        self.msg_list.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.msg_list.setViewMode(QListView.ViewMode.IconMode)
        self.msg_list.setFlow(QListView.Flow.LeftToRight)
        self.msg_list.setWrapping(True)
        self.msg_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.msg_list.setMovement(QListView.Movement.Snap)
        self.msg_list.setGridSize(QSize(380, 250))
        self.msg_list.setSpacing(0)
        self.msg_list.setFrameShape(QFrame.Shape.NoFrame)
        self.msg_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.msg_list.verticalScrollBar().valueChanged.connect(self._on_list_scrolled)
        self.msg_list.itemClicked.connect(self._message_clicked)
        self.msg_list.itemDoubleClicked.connect(self._message_edit)
        self.msg_list.customContextMenuRequested.connect(self._message_context_menu)
        main_layout.addWidget(self.msg_list, 1)

        root.addWidget(sidebar)
        root.addWidget(main_panel, 1)
        self.setCentralWidget(central)

        search_action = QAction(self)
        search_action.setShortcut(QKeySequence("Ctrl+F"))
        search_action.triggered.connect(self.search_input.setFocus)
        self.addAction(search_action)

        self._build_tray()
        self._refresh_groups()
        self._refresh_messages()

        self.sync = SyncManager(self.store, parent=self)
        self.sync.status_changed.connect(lambda *_: self._update_sync_footer())
        self.sync.data_changed.connect(self._on_remote_data)
        self.store.add_change_listener(self.sync.schedule)
        self._sync_clock = QTimer(self)
        self._sync_clock.setInterval(30000)
        self._sync_clock.timeout.connect(self._update_sync_footer)
        self._sync_clock.start()
        self._sync_pull_timer = QTimer(self)
        self._sync_pull_timer.setInterval(5 * 60 * 1000)
        self._sync_pull_timer.timeout.connect(lambda: self.sync.sync_now("periodic", force=False))
        self._sync_pull_timer.start()
        QApplication.instance().aboutToQuit.connect(self.sync.sync_blocking)
        self._update_sync_footer()
        QTimer.singleShot(2500, lambda: self.sync.sync_now("startup", force=True) if self.sync.configured() else None)

    def _import_messages(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "استيراد الرسائل", "", "ebo clipboard (*.zdb);;All files (*)"
        )
        if not path:
            return
        try:
            self.store.import_zdb(path, merge=True)
            self._refresh_groups()
            self._refresh_messages()
            self.refresh_bindings_cb()
            QMessageBox.information(self, "استيراد", "تم استيراد الرسائل بنجاح")
        except Exception as exc:
            show_error_dialog(self, "فشل الاستيراد", str(exc))

    def _export_messages(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "تصدير الرسائل", "backup.zdb", "ebo clipboard (*.zdb)"
        )
        if not path:
            return
        if not path.lower().endswith(".zdb"):
            path += ".zdb"
        try:
            self.store.export_zdb(path)
            QMessageBox.information(self, "تصدير", "تم تصدير الرسائل بنجاح")
        except Exception as exc:
            show_error_dialog(self, "فشل التصدير", str(exc))

    def _add_group(self):
        name, accepted = QInputDialog.getText(self, "مجموعة جديدة", "اسم المجموعة")
        if not accepted or not name.strip():
            return
        self.current_group_id = self.store.add_group(name.strip())
        self._refresh_groups()
        self._refresh_messages()

    def _refresh_groups(self):
        groups = self.store.list_groups()
        group_ids = [group["id"] for group in groups]
        if self.current_group_id not in group_ids:
            self.current_group_id = group_ids[0] if group_ids else None

        self.group_list.blockSignals(True)
        self.group_list.clear()
        selected_row = -1
        for row, group in enumerate(groups):
            item = QListWidgetItem(make_group_icon(), group["name"])
            item.setData(Qt.ItemDataRole.UserRole, group["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, group["name"])
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.group_list.addItem(item)
            if group["id"] == self.current_group_id:
                selected_row = row
        if selected_row >= 0:
            self.group_list.setCurrentRow(selected_row)
        else:
            self.group_list.setCurrentRow(-1)
        self.group_list.blockSignals(False)

    def _apply_search(self):
        self._refresh_messages(reload=False)

    def _refresh_messages(self, reload=True):
        self._stop_card_animations()
        self.msg_list.clear()
        query = normalize_digits(self.search_input.text().strip()).casefold()
        if query:
            groups = self.store.list_groups()
            group_names = {group["id"]: group["name"] for group in groups}
            if reload or self._message_rows_group_id != 0:
                rows = []
                for group in groups:
                    rows.extend(self.store.list_messages(group["id"]))
                self._message_rows = rows
                self._message_rows_group_id = 0
        elif self.current_group_id is None:
            self._message_rows = []
            self._message_rows_group_id = None
            group_names = {}
        else:
            groups = self.store.list_groups()
            group_names = {group["id"]: group["name"] for group in groups}
            if reload or self._message_rows_group_id != self.current_group_id:
                self._message_rows = self.store.list_messages(self.current_group_id)
                self._message_rows_group_id = self.current_group_id

        can_reorder = self.current_group_id is not None and not query
        visible_messages = 0
        for message in self._message_rows:
            searchable_text = normalize_digits(
                f'{message["name"]} {message["content"]}'
            ).casefold()
            if query and query not in searchable_text:
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, message["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, message["name"])
            item.setData(Qt.ItemDataRole.UserRole + 2, message["keybind"] or "")
            item.setData(Qt.ItemDataRole.UserRole + 3, message["flagged"])
            item.setData(Qt.ItemDataRole.UserRole + 4, message["has_image"])
            item.setSizeHint(QSize(380, 250))
            self.msg_list.addItem(item)
            card = MessageCard(
                message,
                group_names.get(message["group_id"], "رسائل") if query else "",
                self.msg_list,
                draggable=can_reorder,
            )
            card.copy_requested.connect(self._copy_message_by_id)
            card.edit_requested.connect(self._edit_message_by_id)
            card.delete_requested.connect(self._delete_message_by_id)
            card.star_toggled.connect(self._toggle_star)
            card.clicked.connect(self._message_clicked_id)
            card.double_clicked.connect(self._edit_message_by_id)
            card.drag_started.connect(self._drag_started)
            card.drag_moved.connect(self._drag_moved)
            card.drag_finished.connect(self._drag_finished)
            self.msg_list.setItemWidget(item, card)
            visible_messages += 1

        self.message_count_label.setText(f"{visible_messages} رسالة")
        self.empty_state.setVisible(visible_messages == 0)
        self.msg_list.setVisible(visible_messages > 0)
        if query:
            self.messages_heading.setText("نتائج البحث")
            self.messages_subtitle.setText("البحث في كل المجموعات")
        else:
            current_group = next(
                (group for group in self.store.list_groups() if group["id"] == self.current_group_id),
                None,
            )
            self.messages_heading.setText(current_group["name"] if current_group else "الرسائل")
            self.messages_subtitle.setText("اسحب أي رسالة لتغيير مكانها")

        self.msg_list.setDragEnabled(False)
        self.msg_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._apply_grid_size()
        QTimer.singleShot(0, self.msg_list.doItemsLayout)

    def _apply_grid_size(self):
        available = max(self.msg_list.width() - 14, CARD_MIN_WIDTH + 20)
        columns = max(1, available // (CARD_MIN_WIDTH + 20))
        cell_width = available // columns
        self.msg_list.setGridSize(QSize(cell_width, CARD_HEIGHT + 14))
        for row in range(self.msg_list.count()):
            item = self.msg_list.item(row)
            item.setSizeHint(QSize(cell_width, CARD_HEIGHT + 14))
            card = self.msg_list.itemWidget(item)
            if card is not None:
                card.setFixedSize(cell_width - 20, CARD_HEIGHT)
        self.msg_list.doItemsLayout()

    def _group_selected(self, current, previous):
        if not current:
            return
        self.current_group_id = current.data(Qt.ItemDataRole.UserRole)
        self._refresh_messages()

    def _rename_group(self, item, group_id):
        current_name = item.data(Qt.ItemDataRole.UserRole + 1) or item.text()
        name, accepted = QInputDialog.getText(
            self, "تعديل المجموعة", "اسم المجموعة", text=current_name
        )
        if accepted and name.strip():
            self.store.rename_group(group_id, name.strip())
            self._refresh_groups()
            self._refresh_messages()

    # ---------- card animation / drag-to-reorder ----------
    def _card_widgets(self):
        cards = []
        for row in range(self.msg_list.count()):
            card = self.msg_list.itemWidget(self.msg_list.item(row))
            if card is not None:
                cards.append(card)
        return cards

    def _stop_card_animations(self):
        animations = list(self._card_animations.values())
        self._card_animations = {}
        for animation in animations:
            animation.stop()
            animation.deleteLater()

    def _animate_card_to(self, card, target, duration=220):
        key = card.message_id
        previous = self._card_animations.pop(key, None)
        if previous is not None:
            previous.stop()
            previous.deleteLater()
        if card.pos() == target:
            return None
        animation = QPropertyAnimation(card, b"pos", self)
        animation.setDuration(duration)
        animation.setStartValue(card.pos())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def done(k=key, a=animation):
            if self._card_animations.get(k) is a:
                del self._card_animations[k]
            a.deleteLater()

        animation.finished.connect(done)
        self._card_animations[key] = animation
        animation.start()
        return animation

    def _refresh_messages_animated(self, reload=True):
        old_positions = {card.message_id: QPoint(card.pos()) for card in self._card_widgets()}
        self._refresh_messages(reload=reload)

        def run():
            for card in self._card_widgets():
                old = old_positions.get(card.message_id)
                new = QPoint(card.pos())
                if old is not None and old != new:
                    card.move(old)
                    self._animate_card_to(card, new, 280)

        QTimer.singleShot(0, run)

    def _drag_started(self, message_id, offset):
        cards = {card.message_id: card for card in self._card_widgets()}
        card = cards.get(message_id)
        if card is None:
            return
        self._stop_card_animations()
        self.msg_list.stop_wheel_animation()
        scroll = self.msg_list.verticalScrollBar().value()
        order = [
            self.msg_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.msg_list.count())
        ]
        slots = [QPoint(cards[mid].pos().x(), cards[mid].pos().y() + scroll) for mid in order]
        self._drag = {
            "id": message_id,
            "offset": QPoint(offset),
            "order": order,
            "initial_order": list(order),
            "slots": slots,
            "size": card.size(),
            "cards": cards,
            "last": None,
            "index": order.index(message_id),
        }
        card.raise_()
        card.setProperty("dragging", True)
        card.style().unpolish(card)
        card.style().polish(card)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 170))
        card.setGraphicsEffect(shadow)
        self._drag_timer.start()

    def _drag_moved(self, message_id, global_pos):
        drag = self._drag
        if not drag or drag["id"] != message_id:
            return
        drag["last"] = QPoint(global_pos)
        self._update_drag(animate=True)

    def _update_drag(self, animate):
        drag = self._drag
        if not drag or drag["last"] is None:
            return
        card = drag["cards"][drag["id"]]
        scroll = self.msg_list.verticalScrollBar().value()
        point = self.msg_list.viewport().mapFromGlobal(drag["last"])
        card.move(point - drag["offset"])
        content = QPoint(point.x(), point.y() + scroll)
        slots, size = drag["slots"], drag["size"]
        target = None
        for index, slot in enumerate(slots):
            if QRect(slot, size).contains(content):
                target = index
                break
        if target is None:
            center = QPoint(size.width() // 2, size.height() // 2)
            target = min(
                range(len(slots)),
                key=lambda i: (slots[i] + center - content).manhattanLength(),
            )
        changed = target != drag["index"]
        if changed:
            drag["order"].insert(target, drag["order"].pop(drag["index"]))
            drag["index"] = target
        if changed or not animate:
            self._layout_drag_cards(animate)

    def _layout_drag_cards(self, animate):
        drag = self._drag
        scroll = self.msg_list.verticalScrollBar().value()
        for index, mid in enumerate(drag["order"]):
            if mid == drag["id"]:
                continue
            card = drag["cards"][mid]
            target = QPoint(drag["slots"][index].x(), drag["slots"][index].y() - scroll)
            if animate:
                self._animate_card_to(card, target, 200)
            else:
                running = self._card_animations.pop(mid, None)
                if running is not None:
                    running.stop()
                    running.deleteLater()
                card.move(target)

    def _on_list_scrolled(self, _value):
        if self._drag:
            self._update_drag(animate=False)
            QTimer.singleShot(0, lambda: self._drag and self._update_drag(animate=False))

    def _drag_autoscroll(self):
        drag = self._drag
        if not drag or drag["last"] is None:
            return
        viewport = self.msg_list.viewport()
        y = viewport.mapFromGlobal(drag["last"]).y()
        edge = 56
        if y < edge:
            delta = -(int((edge - y) / 3) + 5)
        elif y > viewport.height() - edge:
            delta = int((y - (viewport.height() - edge)) / 3) + 5
        else:
            return
        bar = self.msg_list.verticalScrollBar()
        bar.setValue(bar.value() + delta)

    def _drag_finished(self, message_id, global_pos):
        drag = self._drag
        if not drag or drag["id"] != message_id:
            return
        self._drag_timer.stop()
        self._drag = None
        card = drag["cards"][message_id]
        card.setGraphicsEffect(None)
        card.setProperty("dragging", False)
        card.style().unpolish(card)
        card.style().polish(card)

        group_viewport = self.group_list.viewport()
        group_point = group_viewport.mapFromGlobal(global_pos)
        if group_viewport.rect().contains(group_point):
            item = self.group_list.itemAt(group_point)
            if item is not None:
                group_id = item.data(Qt.ItemDataRole.UserRole)
                if group_id != self.current_group_id:
                    self._handle_message_dropped_on_group(message_id, group_id)
                    return

        order = drag["order"]
        scroll = self.msg_list.verticalScrollBar().value()
        slot = drag["slots"][order.index(message_id)]
        target = QPoint(slot.x(), slot.y() - scroll)
        if order == drag["initial_order"]:
            self._animate_card_to(card, target, 200)
            return
        self.store.reorder_messages(self.current_group_id, order)
        position = {mid: index for index, mid in enumerate(order)}
        self._message_rows.sort(key=lambda row: position.get(row["id"], len(order)))
        animation = self._animate_card_to(card, target, 220)
        if animation is None:
            self._refresh_messages()
        else:
            animation.finished.connect(lambda: self._drag is None and self._refresh_messages())

    def _add_message(self):
        if self.current_group_id is None:
            QMessageBox.information(self, "لا توجد مجموعة", "أنشئ مجموعة أولًا.")
            return
        target_group_id = self.current_group_id

        dialog = MessageEditorDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, content, keybind, image = dialog.values()
            if not name:
                return
            self._resolve_keybind_conflict(keybind)
            message_id = self.store.add_message(
                target_group_id, name, content, image=image
            )
            if keybind:
                self.store.set_keybind(message_id, keybind)
            self._refresh_groups()
            self._refresh_messages()
            self.refresh_bindings_cb()

    def _message_edit(self, item):
        self._edit_message_by_id(item.data(Qt.ItemDataRole.UserRole))

    def _edit_message_by_id(self, message_id):
        message = next(
            (row for row in self.store.list_all_messages() if row["id"] == message_id),
            None,
        )
        if not message:
            return
        image = self.store.get_message_image(message_id)
        dialog = MessageEditorDialog(
            self,
            message["name"],
            message["content"],
            message["keybind"],
            image,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, content, keybind, image = dialog.values()
            if not name:
                return
            self._resolve_keybind_conflict(
                keybind, exclude_message_id=message_id
            )
            self.store.update_message(
                message_id, name=name, content=content, image=image
            )
            self.store.set_keybind(message_id, keybind)
            self._refresh_groups()
            self._refresh_messages_animated()
            self.refresh_bindings_cb()

    def _delete_message_by_id(self, message_id):
        if QMessageBox.question(
            self, "حذف الرسالة", "هل تريد حذف هذه الرسالة؟"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_message(message_id)
        self._refresh_groups()
        self._refresh_messages_animated()
        self.refresh_bindings_cb()

    def _toggle_star(self, message_id, starred):
        self.store.set_starred(message_id, starred)
        message = None
        for row in self._message_rows:
            if row["id"] == message_id:
                row["starred"] = starred
                message = row
        if not starred or message is None:
            return
        group_id = message["group_id"]
        ids = [row["id"] for row in self.store.list_messages(group_id)]
        if not ids or ids[0] == message_id:
            return
        ids.remove(message_id)
        ids.insert(0, message_id)
        self.store.reorder_messages(group_id, ids)
        QTimer.singleShot(0, self._refresh_messages_animated)

    def _copy_message_by_id(self, message_id):
        message = next(
            (row for row in self.store.list_all_messages() if row["id"] == message_id),
            None,
        )
        if message and message["flagged"]:
            self.store.set_flagged(message_id, 0)
            self._refresh_messages()
        self.paste_message_by_id(message_id)

    def _apply_responsive_header(self):
        compact = self.width() < COMPACT_HEADER_WIDTH
        self.backup_button.setText("" if compact else "  نسخة احتياطية")
        self.backup_button.setFixedWidth(46 if compact else 190)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_header()
        if not self.isMaximized() and not self.isFullScreen():
            self._window_size_save_timer.start()
        QTimer.singleShot(0, self._apply_grid_size)

    def _save_window_size(self):
        if not self.isMaximized() and not self.isFullScreen():
            self._window_settings.setValue("window/size", self.size())

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_grid_size)
        QTimer.singleShot(0, self.msg_list.doItemsLayout)
        QTimer.singleShot(0, self.msg_list.viewport().update)

    def closeEvent(self, event):
        event.ignore()
        self._save_window_size()
        self.hide()