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

from telegram_sticker_utils import ImageProcessor

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


async def dl(emoji_id):
    client = httpx.AsyncClient()
    # TODO
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
            CommandHandler('updatepack', self.updatepack),
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

    async def modifypack(self, update: Update, context: ContextTypes.DEFAULT_TYPE, is_update: bool):
        bot = context.bot

        if len(context.args) != 2:
            await bot.send_message(
                chat_id=update.effective_chat.id,
                text="Usage: <name of this emojipack> <id of this emojipack in bilibili>"
            )
            return

        provided_name, mid = context.args
        pack_name = f'bili_{provided_name}_by_{self.me}'
        uid = update.effective_user.id

        current_msg_id = 0
        async def update_message(text):
            nonlocal current_msg_id
            if current_msg_id == 0:
                try:
                    msg = await bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=text
                    )
                    print('new msg', msg.id, msg)
                    current_msg_id = msg.id
                except:
                    print('send msg err:', text)
                    traceback.print_exc()
            else:
                try:
                    await bot.edit_message_text(
                        text=text,
                        chat_id=update.effective_chat.id,
                        message_id=current_msg_id
                    )
                except:
                    print('modify msg err:', current_msg_id, text)
                    traceback.print_exc()

        sticker_set_info = None
        current_sticker_count = 0
        try:
            sticker_set_info = await bot.get_sticker_set(pack_name)
            current_sticker_count = len(sticker_set_info.stickers)
        except:
            print('get sticker info')
            sticker_set_info = None

        if is_update and sticker_set_info is None:
            await update_message(f"Sticker set {pack_name} has been already created.")
            return
        elif not is_update and sticker_set_info is not None:
            await update_message(f"Sticker set {pack_name} doesn't exist.")
            return

        is_created = is_update
        emojies = list()

        i = 0
        total_count = 0

        async def upload():
            nonlocal i, is_created, total_count
            it = fetch_emojies(mid, self.client)
            try:
                names = await anext(it)
                total_count = await anext(it)
                # TODO: split into some packs
                if total_count > 200:
                    await update_message(f"too many emojies ({total_count})")
                    return False
            except:
                print('fetch error')
                traceback.print_exc()
                await update_message("FETCH ERROR")
                return False

            async for ename, raw, emoji_list, emoji_type in it:
                emoji = telegram.InputSticker(
                    raw,
                    emoji_list, emoji_type
                )
                if not is_created:
                    print('create', pack_name, 'for', uid, update.effective_user.name)
                    try:
                        await bot.create_new_sticker_set(
                            uid,
                            pack_name,
                            names[0],
                            [emoji],
                            telegram.Sticker.CUSTOM_EMOJI
                        )
                        is_created = True
                    except:
                        print('create err')
                        traceback.print_exc()
                        await update_message(f"CREATE ERROR https://t.me/addemoji/{pack_name}")
                        return False
                else:
                    try:
                        if i >= current_sticker_count:
                            print('add', i)
                            await bot.add_sticker_to_set(uid, pack_name, emoji)
                        else:
                            file_id = sticker_set_info.stickers[i].file_id
                            print('update', i, file_id)
                            await bot.replace_sticker_in_set(
                                user_id=uid, name=pack_name,
                                old_sticker=file_id,
                                sticker=emoji
                            )
                    except:
                        print('add sticker err')
                        traceback.print_exc()
                        await update_message(f"ADD ERROR https://t.me/addemoji/{pack_name}")
                        return False
                i += 1
                emojies.append((ename, emoji_list))
            return True
        aw = [asyncio.create_task(upload())]
        last_i = -1
        while True:
            if aw[0].done():
                break
            if last_i != i:
                last_i = i
                if total_count == 0:
                    await update_message(f"Fetching ...")
                else:
                    await update_message(f"Processing ... [{i}/{total_count}]")
            await asyncio.wait(aw, timeout=3)

        if not aw[0].result():
            return

        await update_message(f"{'Updated' if is_update else 'Created'} {total_count} emojies in https://t.me/addemoji/{pack_name}")
        new_set = await bot.get_sticker_set(pack_name)
        mp = {}
        i = 0
        for s in new_set.stickers:
            print('emoji', i, emojies[i][0], s)
            mp[emojies[i][0]] = [s.custom_emoji_id, emojies[i][1]]
            i += 1
        os.makedirs("storage", exist_ok=True)
        with open(f"storage/{pack_name}.json", 'w', encoding='utf-8') as f:
            f.write(json.dumps(mp, ensure_ascii=False))

    async def createpack(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.modifypack(update, context, False)

    async def updatepack(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.modifypack(update, context, True)

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


def merge():
    np = {}
    for fn in os.listdir('storage'):
        with open('storage/' + fn, 'r', encoding='utf-8') as f:
            data = json.load(f)
        np.update(data)
    with open('out/merged.json', 'w', encoding='utf-8') as f:
        json.dump(np, f, ensure_ascii=False)

if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'dl':
        asyncio.run(dl(sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1] == 'fixup':
        fixup()
    elif len(sys.argv) == 2 and sys.argv[1] == 'merge':
        merge()
    else:
        with open('config.json', 'r', encoding='utf-8') as f:
            token = json.load(f)['token']
        EmojiBot(token).run()