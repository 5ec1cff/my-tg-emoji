import win32clipboard
import win32con
import win32gui
import win32api
import ctypes
import struct
from io import BytesIO
import re
import os
import json

all_emojies = {}

def write_emojies(text):
    s = ''
    off = 0
    tags = []
    while r := re.search(r'\[.*?]', text):
        left, right = r.span()
        s += text[:left]
        off += len(text[:left].encode('utf-16le'))//2
        t = text[left:right]
        if t in all_emojies:
            e = all_emojies[t]
            te = e[1][0]
            s += te
            le = len(te.encode('utf-16be')) // 2
            tags.append(('custom-emoji://' + e[0], off, le))
            off += le
        else:
            s += text[left:right]
            off += len(text[left:right].encode('utf-16le'))//2
        text = text[right:]
    s += text
    print(s)
    print(tags)

    # SerializeTags in
    # https://github.com/desktop-app/lib_ui/blob/2a5d66fb1b9f97eacc3e73c324944a8d77c38e51/ui/text/text_entity.cpp#L1922
    bio = BytesIO()
    bio.write(struct.pack('>I', len(tags)))
    for ts, to, tl in tags :
        bio.write(struct.pack('>II', to, tl))
        emoid = ts.encode('utf-16be')
        bio.write(struct.pack('>I', len(emoid)))
        bio.write(emoid)
    return s.encode('utf-8'), bio.getbuffer().tobytes()


# https://learn.microsoft.com/zh-tw/windows/win32/dataxchg/wm-clipboardupdate
WM_CLIPBOARDUPDATE = 0x031D


class ClipboardMonitor:
    def __init__(self, handler):
        self.hwnd = None
        self.create_window()
        self.handler = handler

    def create_window(self):
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self.wnd_proc
        wc.lpszClassName = "ClipboardMonitor"
        self.hwnd = win32gui.CreateWindow(
            win32gui.RegisterClass(wc),
            "Clipboard Monitor",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            win32api.GetModuleHandle(None),
            None
        )

        ctypes.windll.user32.AddClipboardFormatListener(self.hwnd)

    def wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            self.on_clipboard_update()
        elif msg == win32con.WM_QUIT:
            print('destroy')
            win32gui.DestroyWindow(self.hwnd)
            win32gui.PostQuitMessage(0)
        return 0

    def on_clipboard_update(self):
        self.handler()


if __name__ == '__main__':
    import sys
    import traceback

    for fn in os.listdir('storage'):
        with open('storage/' + fn, 'r', encoding='utf-8') as f:
            data = json.load(f)
            all_emojies.update(data)

    fmt_tags = win32clipboard.RegisterClipboardFormat('application/x-td-field-tags')
    fmt_text = win32clipboard.RegisterClipboardFormat('application/x-td-field-text')
    print('fmt tags', fmt_tags, 'fmt text', fmt_text)

    def rewrite_emoji():
        win32clipboard.OpenClipboard()
        try:
            try:
                win32clipboard.GetClipboardData(fmt_tags)
                print('not handling copy from tg')
                return
            except:
                pass
            text, tags = write_emojies(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT).replace('\r', ''))
            win32clipboard.SetClipboardData(fmt_text, text)
            win32clipboard.SetClipboardData(fmt_tags, tags)
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        except:
            traceback.print_exc()
        finally:
            win32clipboard.CloseClipboard()

    if len(sys.argv) >= 2 and sys.argv[1] == "--daemon":
        monitor = ClipboardMonitor(rewrite_emoji)
        def CtrlHandler(evt):
            if evt in (win32con.CTRL_C_EVENT, win32con.CTRL_BREAK_EVENT):
                #win32gui.DestroyWindow(monitor.hwnd)
                win32gui.SendMessage(monitor.hwnd, win32con.WM_QUIT, 0, 0)
                return True
            return False
        win32api.SetConsoleCtrlHandler(CtrlHandler, True)
        win32gui.PumpMessages()  # Keep the window alive to receive messages
        exit(0)

    rewrite_emoji()
