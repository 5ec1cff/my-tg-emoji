import win32clipboard
import win32con
import win32gui
import win32api
import ctypes
import ctypes.wintypes
import struct
from io import BytesIO
import re
import os
import json
import pathlib
import traceback

all_emojies = {}
_emoji_source_signature = None

FALLBACK_EMOJI = '🥰'


def normalize_emoji(value):
    if isinstance(value, list):
        if len(value) == 0:
            return FALLBACK_EMOJI
        return value[0]
    if value is None:
        return FALLBACK_EMOJI
    return value


def load_storage_emoji_map(data):
    mp = {}

    # new format
    if isinstance(data, dict) and isinstance(data.get('emojis'), list):
        for e in data['emojis']:
            if not isinstance(e, dict):
                continue
            name = e.get('name')
            tg_id = e.get('telegram_custom_emoji_id')
            if name is None or tg_id is None:
                continue
            mp[name] = {
                'telegram_custom_emoji_id': str(tg_id),
                'emoji': normalize_emoji(e.get('emoji')),
            }
        return mp

    # old format: {emoji_name: [telegram_custom_emoji_id, emoji]}
    if not isinstance(data, dict):
        return mp
    for name in data:
        val = data[name]
        tg_id = None
        emoji = FALLBACK_EMOJI
        if isinstance(val, list):
            if len(val) >= 1:
                tg_id = val[0]
            if len(val) >= 2:
                emoji = normalize_emoji(val[1])
        else:
            tg_id = val
        if tg_id is None:
            continue
        mp[name] = {
            'telegram_custom_emoji_id': str(tg_id),
            'emoji': emoji,
        }
    return mp


def get_pack_key_priority(pack_key, key_order):
    if pack_key in key_order:
        return key_order.index(pack_key)
    return len(key_order)


def merge_packs_with_key_order(packs, key_order):
    selected = {}

    for pack_idx, pack in enumerate(packs):
        pack_mp = load_storage_emoji_map(pack)
        pack_key = pack.get('key') if isinstance(pack, dict) else None
        priority = get_pack_key_priority(pack_key, key_order)

        for name, emoji_data in pack_mp.items():
            prev = selected.get(name)
            if prev is None:
                selected[name] = {
                    'priority': priority,
                    'pack_idx': pack_idx,
                    'data': emoji_data,
                }
                continue

            if priority < prev['priority'] or priority == prev['priority'] and pack_idx < prev['pack_idx']:
                selected[name] = {
                    'priority': priority,
                    'pack_idx': pack_idx,
                    'data': emoji_data,
                }

    return {name: entry['data'] for name, entry in selected.items()}


def load_emoji_map_from_data(data):
    # merged format: [pack_payload, ...]
    if isinstance(data, list):
        return merge_packs_with_key_order(data, [])

    # merged wrapper: {'key_order': [...], 'packs': [...]} 
    if isinstance(data, dict) and isinstance(data.get('packs'), list):
        key_order = data.get('key_order', [])
        if not isinstance(key_order, list):
            key_order = []
        return merge_packs_with_key_order(data['packs'], key_order)

    # single payload / legacy map
    return load_storage_emoji_map(data)


def get_file_signature(path: pathlib.Path):
    st = path.stat()
    return st.st_mtime_ns, st.st_size


def reload_emojies_if_needed(json_path: pathlib.Path, force=False):
    global _emoji_source_signature

    try:
        signature = get_file_signature(json_path)
    except FileNotFoundError:
        if force:
            print(f'emoji json not found: {json_path}')
        return False

    if (not force) and signature == _emoji_source_signature:
        return False

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    loaded = load_emoji_map_from_data(data)

    all_emojies.clear()
    all_emojies.update(loaded)
    _emoji_source_signature = signature

    print(f'reloaded {len(all_emojies)} emojis from {json_path}')
    return True


def parse_cli_args(argv):
    json_path = 'out/merged.json'
    daemon_mode = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--daemon':
            daemon_mode = True
        elif arg == '--json':
            i += 1
            if i >= len(argv):
                raise ValueError('--json requires a file path')
            json_path = argv[i]
        elif arg.startswith('--json='):
            json_path = arg.split('=', 1)[1]
        elif arg.startswith('--'):
            raise ValueError(f'unknown option: {arg}')
        else:
            json_path = arg
        i += 1

    return pathlib.Path(json_path), daemon_mode

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
            te = e['emoji']
            s += te
            le = len(te.encode('utf-16be')) // 2
            tags.append(('custom-emoji://' + e['telegram_custom_emoji_id'], off, le))
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


SetClipboardData = ctypes.windll.user32.SetClipboardData
GlobalLock          = ctypes.windll.kernel32.GlobalLock
GlobalLock.restype = ctypes.c_void_p
GlobalAlloc         = ctypes.windll.kernel32.GlobalAlloc
GlobalAlloc.restype = ctypes.c_void_p
GlobalUnlock        = ctypes.windll.kernel32.GlobalUnlock
memcpy              = ctypes.cdll.msvcrt.memcpy
CF_TEXT = 1
GHND                = 0x42

# https://github.com/creotiv/pynetbuffer/blob/ce73ac6798fd883ae678468b2df8c5db4e603087/win/main.py#L85
# https://learn.microsoft.com/en-us/windows/win32/dataxchg/using-the-clipboard#copy-information-to-the-clipboard
# https://learn.microsoft.com/zh-cn/windows/win32/api/winbase/nf-winbase-globalalloc
def SetClipboard(type, text):
    buffer = ctypes.c_buffer(text)
    bufferSize = ctypes.sizeof(buffer)
    hGlobalMem = ctypes.c_void_p(GlobalAlloc(GHND, bufferSize))
    try:
        lpGlobalMem = ctypes.c_void_p(GlobalLock(hGlobalMem))
        addr = ctypes.c_void_p(ctypes.addressof(buffer))
        memcpy(lpGlobalMem, addr, bufferSize)
    finally:
        GlobalUnlock(hGlobalMem)
    SetClipboardData(type, hGlobalMem)


if __name__ == '__main__':
    import sys

    try:
        source_json_path, daemon_mode = parse_cli_args(sys.argv[1:])
    except ValueError as e:
        print(e)
        print('usage: python clip.py [json_path] [--daemon]')
        print('   or: python clip.py --json <json_path> [--daemon]')
        exit(1)

    try:
        reload_emojies_if_needed(source_json_path, force=True)
    except Exception:
        print(f'failed to load emoji json: {source_json_path}')
        traceback.print_exc()
        exit(1)

    fmt_tags = win32clipboard.RegisterClipboardFormat('application/x-td-field-tags')
    fmt_text = win32clipboard.RegisterClipboardFormat('application/x-td-field-text')
    print('fmt tags', fmt_tags, 'fmt text', fmt_text)

    def rewrite_emoji():
        try:
            reload_emojies_if_needed(source_json_path)
        except Exception:
            print(f'failed to reload emoji json: {source_json_path}')
            traceback.print_exc()
        win32clipboard.OpenClipboard()
        try:
            try:
                win32clipboard.GetClipboardData(fmt_tags)
                print('not handling copy from tg')
                return
            except:
                pass
            try:
                orig_text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT).replace('\r', '')
            except:
                return
            text, tags = write_emojies(orig_text)
            # win32clipboard's bug crashes when set pure number content, so use ctypes
            SetClipboard(fmt_text, text)
            SetClipboard(fmt_tags, tags)
            print('done')
        except:
            traceback.print_exc()
        finally:
            win32clipboard.CloseClipboard()

    if daemon_mode:
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
