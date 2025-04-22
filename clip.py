import win32clipboard
import struct
from io import BytesIO
import re
import os
import json

def write_emojies(text):
    all_emojies = {}
    for fn in os.listdir('storage'):
        with open('storage/' + fn, 'r', encoding='utf-8') as f:
            data = json.load(f)
            all_emojies.update(data)
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
            uni = ord(te)
            le = 2 if uni > 0xffff else 1
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

if __name__ == '__main__':
    win32clipboard.OpenClipboard()
    try:
        fmt_tags = win32clipboard.RegisterClipboardFormat('application/x-td-field-tags')
        fmt_text = win32clipboard.RegisterClipboardFormat('application/x-td-field-text')
        print('fmt tags', fmt_tags, 'fmt text', fmt_text)

        text, tags = write_emojies(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT).replace('\r', ''))
        win32clipboard.SetClipboardData(fmt_text, text)
        win32clipboard.SetClipboardData(fmt_tags, tags)
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    except:
        import traceback

        traceback.print_exc()

    win32clipboard.CloseClipboard()
