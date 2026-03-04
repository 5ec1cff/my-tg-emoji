import io
import json
import os
import pathlib
import re
import sys
import traceback
import tempfile

import httpx
import asyncio
import telegram
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

from telegram_sticker_utils import ImageProcessor, Sticker


FALLBACK_EMOJI = '🥰'


def normalize_emoji(value):
    if isinstance(value, list):
        if len(value) == 0:
            return FALLBACK_EMOJI
        return value[0]
    if value is None:
        return FALLBACK_EMOJI
    return value


def infer_source_platform(filename):
    if filename.startswith('bili_'):
        return 'bilibili'
    if filename.startswith('tieba_'):
        return 'tieba'
    if filename.startswith('coolapk_'):
        return 'coolapk'
    return 'unknown'


def build_storage_payload(telegram_pack_name, bilibili_pack_id, emojis):
    return {
        'version': 2,
        'telegram_pack_name': telegram_pack_name,
        'bilibili_pack_id': str(bilibili_pack_id) if bilibili_pack_id is not None else None,
        'emojis': emojis,
    }


def infer_bilibili_pack_id_from_cache(emoji_names):
    packs_root = pathlib.Path('packs')
    if not packs_root.exists():
        return None
    for p in packs_root.iterdir():
        if not p.is_dir():
            continue
        data_file = p / 'data.json'
        if not data_file.exists():
            continue
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            cached_names = [e.get('name') for e in cached.get('emoji_list', [])]
            if cached_names == emoji_names:
                return p.name
        except:
            traceback.print_exc()
    return None


def migrate_storage_data(data, filename):
    # new format
    if isinstance(data, dict) and isinstance(data.get('emojis'), list):
        emojis = []
        for i, e in enumerate(data.get('emojis', [])):
            if not isinstance(e, dict):
                continue
            name = e.get('name')
            if name is None:
                continue
            emojis.append({
                'index': int(e.get('index', i)),
                'name': name,
                'telegram_custom_emoji_id': str(e.get('telegram_custom_emoji_id')),
                'emoji': normalize_emoji(e.get('emoji', FALLBACK_EMOJI)),
            })
        return build_storage_payload(
            data.get('telegram_pack_name', filename[:-5]),
            data.get('bilibili_pack_id'),
            emojis,
        )

    # old format: {emoji_name: [telegram_custom_emoji_id, emoji]}
    if not isinstance(data, dict):
        raise ValueError(f'unsupported storage format: {filename}')

    emojis = []
    emoji_names = []
    for i, name in enumerate(data):
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
        emoji_names.append(name)
        emojis.append({
            'index': i,
            'name': name,
            'telegram_custom_emoji_id': str(tg_id),
            'emoji': emoji,
        })

    bilibili_pack_id = None
    if infer_source_platform(filename) == 'bilibili':
        bilibili_pack_id = infer_bilibili_pack_id_from_cache(emoji_names)

    return build_storage_payload(filename[:-5], bilibili_pack_id, emojis)


def migrate_storage_files():
    os.makedirs('storage', exist_ok=True)
    for fn in os.listdir('storage'):
        if not fn.endswith('.json'):
            continue
        file_path = pathlib.Path('storage') / fn
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        migrated = migrate_storage_data(data, fn)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(migrated, f, ensure_ascii=False, indent=2)


async def get_emoji_data(name, url, client: httpx.AsyncClient):
    for i in range(3):
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise ValueError(f'resp not 200: {resp.status_code}')
            r = resp.read()
            sticker = await asyncio.to_thread(ImageProcessor.make_sticker,
                                              input_name=name, input_data=r, scale=100)
            return sticker.data, sticker.emojis, sticker.sticker_type
        except:
            print('error while getting', url)
            if i == 2:
                break
            await asyncio.sleep(1)
            traceback.print_exc()
    raise RuntimeError(f"cannot get emoji {name} {url}")

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
            yield name, url


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

    async def download_one_emoji(self, out_dir, url, name, j):
        i = 0
        while True:
            try:
                resp = await self.client.get(url)
                if resp.status_code != 200:
                    raise ValueError(f'resp not 200: {resp.status_code}')
                r = resp.read()
                sticker: Sticker = await asyncio.to_thread(ImageProcessor.make_sticker,
                                                           input_name=name, input_data=r, scale=100)
                with open(out_dir / str(j), 'wb') as f:
                    f.write(sticker.data)
                obj = {
                    'name': name,
                    'emoji': sticker.emojis,
                    'type': sticker.sticker_type
                }
                return obj
            except:
                i += 1
                print('error while getting', url)
                if i == 3:
                    raise RuntimeError(f"cannot get emoji {name} {url}")
                await asyncio.sleep(1)
                traceback.print_exc()

    async def prepare_emoji_pack(self, out_dir: pathlib.Path, pack_id):
        resp = await self.client.get('https://api.bilibili.com/x/emote/package',
            params={'ids': pack_id, 'business': 'reply'},
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'}
        )
        #t = resp.text
        #print(t)
        #data = json.loads(t)
        data = resp.json()
        out_data = {}
        pack = data['data']['packages'][0]
        out_data['pack_name'] = pack['text']
        tasks = []
        i = 0
        for emoji in pack['emote']:
            url = emoji.get('gif_url', emoji.get('url'))
            name = emoji['text']
            tasks.append(asyncio.create_task(self.download_one_emoji(out_dir, url, name, i)))
            i += 1
        emoji_list = []
        for t in tasks:
            emoji_list.append(await t)
        out_data['emoji_list'] = emoji_list
        with open(out_dir / 'data.json', 'w') as f:
            json.dump(out_data, f)

    async def modifypack(self, update: Update, context: ContextTypes.DEFAULT_TYPE, is_update: bool):
        bot = context.bot

        if len(context.args) < 2:
            await bot.send_message(
                chat_id=update.effective_chat.id,
                text="Usage: <name of this emojipack> <id of this emojipack in bilibili>"
            )
            return

        skip_upload = len(context.args) >= 3 and context.args[2] == 'skip'
        upload_from = context.args[3] if len(context.args) >= 4 and context.args[2] == 'from' else None
        print(f'{skip_upload = }')

        provided_name, mid = context.args[:2]
        uid = update.effective_user.id
        PACK_MAX_COUNT = 200

        out_dir = pathlib.Path('packs') / f'{mid}'
        out_dir.mkdir(parents=True, exist_ok=True)
        print('working on', out_dir)

        if os.path.exists(out_dir / 'data.json'):
            print('use cached pack')
        else:
            await self.prepare_emoji_pack(out_dir, mid)

        with open(out_dir / 'data.json', 'r') as f:
            emoji_pack_data = json.load(f)

        emoji_list: list = emoji_pack_data['emoji_list']

        for i in range(len(emoji_list)):
            with open(out_dir / f'{i}', 'rb') as f:
                emoji_list[i]["data"] = f.read()

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

        total_count = len(emoji_list)
        total_packs = (total_count + PACK_MAX_COUNT - 1) // PACK_MAX_COUNT

        start_global_idx = 0
        if not skip_upload:
            if upload_from is None:
                start_global_idx = 0
            elif upload_from == 's':
                start_global_idx = 0
            else:
                start_global_idx = int(upload_from)
            print(f'upload from emoji index {start_global_idx}')

        all_messages = []

        for pack_idx in range(total_packs):
            pack_start = pack_idx * PACK_MAX_COUNT
            pack_end = min((pack_idx + 1) * PACK_MAX_COUNT, total_count)
            pack_emojis = emoji_list[pack_start:pack_end]

            if pack_idx == 0:
                pack_name = f'bili_{provided_name}_by_{self.me}'
            else:
                pack_name = f'bili_{provided_name}_{pack_idx}_by_{self.me}'

            print(f'Processing pack {pack_idx}: {pack_name} (emojis {pack_start}-{pack_end-1})')

            sticker_set_info = None
            current_sticker_count = 0
            try:
                sticker_set_info = await bot.get_sticker_set(pack_name)
                current_sticker_count = len(sticker_set_info.stickers)
            except:
                print(f'sticker set {pack_name} not found')
                sticker_set_info = None
            if pack_idx == 0:
                if not is_update and sticker_set_info is not None:
                    await update_message(f"Sticker set {pack_name} has been already created.")
                    return
                elif is_update and sticker_set_info is None:
                    await update_message(f"Sticker set {pack_name} doesn't exist.")
                    return

            # 判断是否需要执行上传
            need_upload = False
            if not skip_upload and start_global_idx < pack_end:
                need_upload = True

            if need_upload:

                is_created = sticker_set_info is not None

                start_local_idx = max(0, start_global_idx - pack_start)
                from_list = pack_emojis[start_local_idx:]
                current_emoji_idx = pack_start + start_local_idx

                async def upload_pack():
                    nonlocal is_created, current_emoji_idx
                    for emoji_data in from_list:
                        emoji = telegram.InputSticker(
                            emoji_data['data'],
                            emoji_data['emoji'], emoji_data['type']
                        )
                        if not is_created:
                            print(f'create {pack_name} for {uid}')
                            try:
                                await bot.create_new_sticker_set(
                                    uid,
                                    pack_name,
                                    emoji_pack_data['pack_name'],
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
                            local_idx = current_emoji_idx - pack_start
                            try:
                                if local_idx >= current_sticker_count:
                                    print(f'add {current_emoji_idx} to {pack_name}')
                                    await bot.add_sticker_to_set(uid, pack_name, emoji)
                                else:
                                    file_id = sticker_set_info.stickers[local_idx].file_id
                                    print(f'update {current_emoji_idx} in {pack_name}')
                                    await bot.replace_sticker_in_set(
                                        user_id=uid, name=pack_name,
                                        old_sticker=file_id,
                                        sticker=emoji
                                    )
                            except:
                                print('add sticker err')
                                traceback.print_exc()
                                await update_message(f"ADD ERROR https://t.me/addemoji/{pack_name} at idx {current_emoji_idx}")
                                return False
                        current_emoji_idx += 1
                    return True

                if is_update and current_sticker_count > len(pack_emojis):
                    print(f'removing redundant emoji from {pack_name}...')
                    for stk in sticker_set_info.stickers[len(pack_emojis):]:
                        try:
                            await bot.delete_sticker_from_set(stk)
                        except:
                            print('remove redundant sticker err')
                            traceback.print_exc()

                aw = [asyncio.create_task(upload_pack())]
                last_idx = -1
                while True:
                    if aw[0].done():
                        break
                    if last_idx != current_emoji_idx:
                        last_idx = current_emoji_idx
                        await update_message(f"Pack {pack_idx}/{total_packs-1}: https://t.me/addemoji/{pack_name}\nProcessing ... [{current_emoji_idx}/{total_count}]")
                    await asyncio.wait(aw, timeout=3)

                if not aw[0].result():
                    return

            # 为所有包建立映射（无论是否执行了上传）
            try:
                new_set = await bot.get_sticker_set(pack_name)
                emojis = []
                for i, s in enumerate(new_set.stickers):
                    if pack_start + i >= len(emoji_list):
                        break
                    emoji = emoji_list[pack_start + i]
                    print(f'emoji {pack_start + i}: {emoji["name"]}')
                    emojis.append({
                        'index': i,
                        'name': emoji['name'],
                        'telegram_custom_emoji_id': str(s.custom_emoji_id),
                        'emoji': normalize_emoji(emoji['emoji']),
                    })
                storage_payload = build_storage_payload(pack_name, mid, emojis)
                os.makedirs("storage", exist_ok=True)
                with open(f"storage/{pack_name}.json", 'w', encoding='utf-8') as f:
                    json.dump(storage_payload, f, ensure_ascii=False, indent=2)
                all_messages.append(f"Pack {pack_idx}: https://t.me/addemoji/{pack_name} ({len(emojis)} emojis)")
            except Exception as e:
                print(f'Failed to retrieve sticker set {pack_name}: {e}')
                all_messages.append(f"Pack {pack_idx}: {pack_name} - failed to retrieve")

        result_text = f"{'Updated' if is_update else 'Created'} {total_count} emojis in {total_packs} pack(s):\n" + "\n".join(all_messages)
        await update_message(result_text)

    async def createpack(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.modifypack(update, context, False)

    async def updatepack(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.modifypack(update, context, True)

    async def text2emoji(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def emoji2text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass


def fixup():
    migrate_storage_files()


def merge():
    merged_packs = []
    key_rules = [("bl", r"^bili_"), ("tb", r"tieba_"), ("ka", r"^coolapk_")]
    for fn in sorted(os.listdir('storage')):
        if not fn.endswith('.json'):
            continue
        with open('storage/' + fn, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for k, r in key_rules:
                if re.match(r, fn):
                    data['key'] = k
        merged_packs.append(data)

    os.makedirs('out', exist_ok=True)
    merged_data = {
        'key_order': ['bl', 'tb', 'ka'],
        'packs': merged_packs,
    }
    with open('out/merged.json', 'w', encoding='utf-8') as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'dl':
        asyncio.run(dl(sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1] in ('fixup', 'migrate'):
        fixup()
    elif len(sys.argv) == 2 and sys.argv[1] == 'merge':
        merge()
    else:
        with open('config.json', 'r', encoding='utf-8') as f:
            token = json.load(f)['token']
        EmojiBot(token).run()