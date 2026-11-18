"""مشغّل ebo clipboard: exe صغير بيشغّل أحدث نسخة من الكود مباشرة.

مفيش نسخة من البرنامج جوه الـ exe ده، هو بس بيشغّل main.py من فولدر المشروع
بـ pythonw الخاص بالـ .venv. يعني أي تعديل في الكود بيظهر من أول فتح بعده،
من غير ما تعمل build تاني.

البناء (مرة واحدة بس):
    python -m PyInstaller --onefile --noconsole --name "ebo clipboard" --icon icon.png --distpath dist launcher.py
"""
import ctypes
import os
import subprocess
import sys

APP_TITLE = "ebo clipboard"


def find_project_dir():
    """فولدر المشروع: جنب الـ exe، أو الفولدر اللي فوقه (لما الـ exe يكون في dist)."""
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    candidates = [os.environ.get("EBO_CLIPBOARD_DIR"), here, os.path.dirname(here)]
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, "main.py")):
            return path
    return None


def find_python(project_dir):
    for path in (
        os.path.join(project_dir, ".venv", "Scripts", "pythonw.exe"),
        os.path.join(project_dir, "venv", "Scripts", "pythonw.exe"),
    ):
        if os.path.isfile(path):
            return path
    return None


def show_error(text):
    ctypes.windll.user32.MessageBoxW(None, text, APP_TITLE, 0x10)


def main():
    project_dir = find_project_dir()
    if project_dir is None:
        show_error("مش لاقي main.py.\nحط الـ exe جوه فولدر المشروع أو جوه dist.")
        return 1
    python = find_python(project_dir)
    if python is None:
        show_error(f"مش لاقي .venv في:\n{project_dir}\n\nاعمل: python -m venv .venv ثم pip install -r requirements.txt")
        return 1
    subprocess.Popen(
        [python, os.path.join(project_dir, "main.py"), *sys.argv[1:]],
        cwd=project_dir,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
