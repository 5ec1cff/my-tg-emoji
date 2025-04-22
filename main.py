import io
import json
import os
import sys
import traceback

import httpx
import asyncio
import telegram
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from asyncio.subprocess import create_subprocess_exec, PIPE
from pathlib import Path
from PIL import Image


async def fetch_emojies(ids, client: httpx.AsyncClient):
    resp = await client.get('https://api.bilibili.com/x/emote/package',
                                 params={'ids': ids, 'business': 'reply'},
                                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'}
                            )
    #t = resp.text
    #print(t)
    #data = json.loads(t)
    data = resp.json()
    result = []
    names = []
    for pack in data['data']['packages']:
        names.append(pack['text'])
        for emoji in pack['emote']:
            if 'gif_url' in emoji:
                ffmpeg_proc = await create_subprocess_exec(
                    "ffmpeg",
                    *"-i - -c vp9 -b:v 0 -crf 40 -f webm -s 100x100 -".split(' '), stdin=PIPE, stdout=PIPE
                )
                ffmpeg_in = ffmpeg_proc.stdin
                ffmpeg_out = ffmpeg_proc.stdout
                async with client.stream('GET', emoji['gif_url']) as r:
                    async for block in r.aiter_bytes():
                        ffmpeg_in.write(block)
                        # tmp_fn = f'{emoji["package_id"]}:{emoji["id"]}.gif'
                ffmpeg_in.close()
                converted = await ffmpeg_out.read()
                result.append((emoji['text'], converted, True))
            else:
                r = await client.get(emoji['url'])
                img = Image.open(io.BytesIO(r.read()))
                img = img.resize((100, 100), Image.Resampling.BICUBIC)
                o = io.BytesIO()
                img.save(o, format='PNG')
                result.append((emoji['text'], o.getbuffer().tobytes(), False))
    return names, result


async def dl(emoji_id):
    client = httpx.AsyncClient()
    _, r = await fetch_emojies(emoji_id, client)
    os.makedirs('out', exist_ok=True)
    for name, data, gif in r:
        with open('out/' + name + ('.png' if not gif else '.webm'), 'wb') as f:
            f.write(data)


class EmojiBot:
    def __init__(self, token):
        self.client = httpx.AsyncClient()
        self.app = ApplicationBuilder().token(token).build()
        self.me = ''
        self.app.add_handlers([
            CommandHandler('createpack', self.createpack),
            CommandHandler('emoji2text', self.emoji2text),
            CommandHandler('text2emoji', self.text2emoji),
        ])
        self.bot: telegram.Bot = self.app.bot

    async def init(self):
        me = await self.bot.get_me()
        print('got me', me)
        self.me = me.username

    def run(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        asyncio.get_event_loop().run_until_complete(self.init())
        self.app.run_polling()

    async def createpack(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        bot = context.bot

        if len(context.args) != 2:
            await bot.send_message(
                chat_id=update.effective_chat.id,
                text="Usage: <name of this emojipack> <id of this emojipack in bilibili>"
            )
            return

        name, mid = context.args

        try:
            names, data = await fetch_emojies(mid, self.client)
        except:
            print('fetch error')
            traceback.print_exc()
            await bot.send_message(
                chat_id=update.effective_chat.id,
                text="FETCH ERROR"
            )
            return

        emojies = list()
        for _, raw, gif in data:
            emoji = telegram.InputSticker(
                raw,
                ['🥰'], 'video' if gif else 'static'
            )
            emojies.append(emoji)
        tg_name = f'bili_{name}_by_{self.me}'
        uid = update.effective_user.id
        print('create', tg_name, 'for', uid, update.effective_user.name)

        await bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"creating https://t.me/addemoji/{tg_name}"
        )
        # bot.edit_message_text('', update.effective_chat.id, msg.id)
        try:
            await bot.create_new_sticker_set(
                uid,
                tg_name,
                names[0],
                emojies[:20],
                telegram.Sticker.CUSTOM_EMOJI
            )
        except:
            print('create err')
            traceback.print_exc()
            await bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"CREATE ERROR https://t.me/addemoji/{tg_name}"
            )
        for i in range(20, len(emojies)):
            try:
                await bot.add_sticker_to_set(uid, tg_name, emojies[i])
            except:
                print('create err')
                traceback.print_exc()
                await bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"ADD ERROR https://t.me/addemoji/{tg_name}"
                )
                return
        new_set = await bot.get_sticker_set(tg_name)
        mp = {}
        i = 0
        for s in new_set.stickers:
            print('emoji', i, data[i][0], s)
            mp[data[i][0]] = [s.custom_emoji_id, '🥰']
            i += 1
        os.makedirs("storage", exist_ok=True)
        with open(f"storage/{tg_name}.json", 'w', encoding='utf-8') as f:
            f.write(json.dumps(mp, ensure_ascii=False))
        await bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"created https://t.me/addemoji/{tg_name}"
        )


    async def text2emoji(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def emoji2text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass


def fixup():
    for fn in os.listdir('storage'):
        with open('storage/' + fn, 'r', encoding='utf-8') as f:
            data = json.load(f)
        np = {}
        for e in data:
            if not isinstance(data[e], list):
                np[e] = [data[e], '🥰']
            else:
                np[e] = data[e]
        with open('storage/' + fn, 'w', encoding='utf-8') as f:
            json.dump(np, f, ensure_ascii=False)



if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'dl':
        asyncio.run(dl(sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1] == 'fixup':
        fixup()
    else:
        with open('config.json', 'r', encoding='utf-8') as f:
            token = json.load(f)['token']
        EmojiBot(token).run()