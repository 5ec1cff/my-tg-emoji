import io
import json
import os
import sys
import traceback
from pathlib import Path
import httpx
import asyncio
import telegram
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from PIL import Image

from telegram_sticker_utils import ImageProcessor


with open('config.json', 'r') as f:
    config = json.load(f)
    ROOT = Path(config['upload_dir'])
    MY_UID = config['my_uid']
    TOKEN = config['token']


async def fetch_emojies(ids, client: httpx.AsyncClient):
    resp = await client.get('https://api.bilibili.com/x/emote/package',
        params={'ids': ids, 'business': 'reply'},
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'}
    )
    #t = resp.text
    #print(t)
    #data = json.loads(t)
    data = resp.json()
    names = []
    for pack in data['data']['packages']:
        names.append(pack['text'])
    yield names

    total = 0
    for pack in data['data']['packages']:
        total += len(pack['emote'])
    yield total
    for pack in data['data']['packages']:
        for emoji in pack['emote']:
            url = emoji.get('gif_url', emoji.get('url'))
            name = emoji['text']
            r = (await client.get(url)).read()
            sticker = await asyncio.to_thread(ImageProcessor.make_sticker,
                                                input_name=name, input_data=r, scale=100)
            yield name, sticker.data, sticker.emojis, sticker.sticker_type

def process():
    root = ROOT
    pngroot = root / "png"
    outroot = root / "proceed"
    os.makedirs(outroot, exist_ok=True)
    ls = sorted(os.listdir(pngroot))
    i = 0
    for fn in ls:
        if not fn.endswith(".png"):
            continue
        with open(pngroot / fn, 'rb') as f:
            data = f.read()
        proceed = ImageProcessor.make_sticker(input_name=fn, input_data=data, scale=512)
        with open(outroot / fn, 'wb') as f:
            f.write(proceed.data)
        i += 1

real_emoji_list = [
    '👆', '❤️', '🐇', '🎤', '🤗', '😠', '😉', '🐇', '🐱',
    '👆', '🐇', '🤗', '😠', '😉', '🐱',
    '☺️', '🍵', '😫', '😰', '🦊', '❤️', '😠', '🤗', '🥵', '😠',
    '🐱', '🧃', '❓', '😈', '😠', '😢', '👍🏻', '❤️', '👌🏻',
    '😎', '🐶', '🍵', '👍🏻', '👹', '😨', '😢', '🥺', '❤️',
    '🧃', '✌🏻', '❤️', '🙃', '😎', '😔', '❓', '🐺', '👍🏻'
]

async def main(*upd_name):
    bot = telegram.Bot(TOKEN)
    root = ROOT / 'proceed'
    ls = sorted(os.listdir(root))
    emojies = []
    i = 0
    for fn in ls:
        print(i, real_emoji_list[i], fn)
        with open(root / fn, 'rb') as f:
            data = f.read()
        emoji = telegram.InputSticker(
            data,
            [real_emoji_list[i]], 'static'
        )
        emojies.append(emoji)
        i += 1
    async with bot:
        me = await bot.get_me()
        print(me)
        uid = MY_UID
        set_name = f'bili_22855779_by_{me.username}'
        title = 'Bilibili@搁浅的月光'

        sticker_set_info = None
        current_sticker_count = 0
        try:
            sticker_set_info = await bot.get_sticker_set(set_name)
            current_sticker_count = len(sticker_set_info.stickers)
        except:
            print('get sticker info')
            sticker_set_info = None

        if len(upd_name) == 0 and sticker_set_info is None:
            init_emojies = emojies[:20]
            print('create', set_name, len(init_emojies))
            await bot.create_new_sticker_set(
                uid,
                set_name,
                title,
                init_emojies,
                telegram.Sticker.REGULAR
            )
            j = 20
            for emoji in emojies[20:]:
                print('adding remain', j)
                await bot.add_sticker_to_set(uid, set_name, emoji)
                j += 1
        else:
            upd_map = {}
            if len(upd_name) != 0:
                for n in upd_name:
                    idx = ls.index(n)
                    if idx == -1:
                        print(n, 'not found')
                    else:
                        print(n, 'idx', idx)
                        upd_map[idx] = emojies[idx]
            else:
                for i, emj in enumerate(emojies):
                    upd_map[i] = emj
            print('updating', len(upd_map), 'emojies', upd_map)
            for i in upd_map:
                emoji = upd_map[i]
                print(i, emoji)
                if i >= current_sticker_count:
                    print('add', i)
                    # should fail to keep order if we can't add
                    await bot.add_sticker_to_set(uid, set_name, emoji)
                else:
                    file_id = sticker_set_info.stickers[i].file_id
                    print('update', i, file_id)
                    try:
                        await bot.replace_sticker_in_set(
                            user_id=uid, name=set_name,
                            old_sticker=file_id,
                            sticker=emoji
                        )
                    except:
                        print('failed')
                        traceback.print_exc()

if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'process':
        process()
    elif len(sys.argv) >= 2:
        asyncio.run(main(*sys.argv[1:]))
    else:
        asyncio.run(main())
