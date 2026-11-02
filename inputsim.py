import time
import pyperclip
import keyboard
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication



def set_clipboard(text):
    pyperclip.copy(text)


def paste_image(image_data):
    image = QImage.fromData(image_data)
    if image.isNull():
        raise ValueError("Saved clipboard image could not be loaded")
    QApplication.clipboard().setImage(image)
    time.sleep(0.05)
    paste()


def paste():
    keyboard.send("ctrl+v")


def paste_text(text):
    set_clipboard(text)
    time.sleep(0.05)
    paste()


def simulate_backspace():
    keyboard.send("backspace")


def simulate_numpad_enter():
    keyboard.send("enter")


def simulate_tab():
    keyboard.send("tab")