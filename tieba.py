import io
import json
import os
import re
import sys
import traceback
from pathlib import Path
import httpx
import asyncio
import telegram
from telegram import InputSticker

from telegram_sticker_utils import ImageProcessor

with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)
    TOKEN = config['token']
    MY_UID = config['my_uid']


FALLBACK_EMOJI = '🥰'


def normalize_emoji(value):
    if isinstance(value, list):
        if len(value) == 0:
            return FALLBACK_EMOJI
        return value[0]
    if value is None:
        return FALLBACK_EMOJI
    return value


def build_storage_payload(telegram_pack_name, bilibili_pack_id, emojis):
    return {
        'version': 2,
        'telegram_pack_name': telegram_pack_name,
        'bilibili_pack_id': str(bilibili_pack_id) if bilibili_pack_id is not None else None,
        'emojis': emojis,
    }

def process_emojies(path: Path):
    out_dir = path / 'proceed'
    out_json = path / 'proceed.json'
    if os.path.exists(out_json):
        print('skip process since proceed exists')
        return
    print('processing ...')
    with open(path / 'map.json', 'r', encoding='utf-8') as f:
        e_map = json.load(f)
    os.makedirs(out_dir, exist_ok=True)
    ls = os.listdir(path)
    out_data = []
    for n in e_map:
        k = list(n.keys())[0]
        v = n[k]
        filename = None
        vdot = v + '.'
        for nn in ls:
            if nn.startswith(vdot):
                filename = nn
                break
        if filename is None:
            raise ValueError(f'not found: {v}')
        with open(path / filename, 'rb') as ef:
            sticker = ImageProcessor.make_sticker(input_name = k, input_data = ef.read(), scale = 100)
        with open(out_dir / filename, 'wb') as wf:
            wf.write(sticker.data)
        out_data.append([filename, k, sticker.emojis, sticker.sticker_type])
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)


PACK_MAX_COUNT = 120

async def upload_emojies(token, name, path: Path, from_idx=0):
    bot = telegram.Bot(token)
    proceed_dir = path / 'proceed'
    proceed_json = path / 'proceed.json'

    with open(proceed_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    async with bot:
        me = await bot.get_me()

        i = from_idx
        sticker_set_info = None
        current_sticker_count = 0
        for emoji in data[from_idx:]:
            pack_id = i // PACK_MAX_COUNT
            emoji_pack_idx = i % PACK_MAX_COUNT
            set_name = f'{name}_{pack_id}_by_{me.username}'
            print(f'adding {i} to pack={pack_id} idx={emoji_pack_idx} name={set_name}')

            filename, key, emojistr, etype = emoji

            with open(proceed_dir / filename, 'rb') as f:
                edata = f.read()

            sticker = InputSticker(edata, emojistr, etype)

            if sticker_set_info is None:
                print(f'getting info for {set_name}')
                try:
                    sticker_set_info = await bot.get_sticker_set(set_name)
                    current_sticker_count = len(sticker_set_info.stickers)
                except telegram.error.BadRequest as e:
                    if e.message == 'Stickerset_invalid':
                        sticker_set_info = None
                    else:
                        raise e

            if sticker_set_info is None:
                if emoji_pack_idx != 0:
                    raise ValueError('create pack need emoji idx 0 !')
                print(f'creating "{set_name}" ...')
                await bot.create_new_sticker_set(
                    MY_UID,
                    set_name,
                    name,
                    [sticker],
                    telegram.Sticker.CUSTOM_EMOJI
                )
                sticker_set_info = None
            else:
                if current_sticker_count < emoji_pack_idx:
                    raise ValueError(f'ERR: not enough emoji to add: current {current_sticker_count}, to add {emoji_pack_idx}')
                elif current_sticker_count > emoji_pack_idx:
                    print(f'WARN: current sticker count {current_sticker_count} > idx {emoji_pack_idx}, some emoji will be overwrite!')
                if emoji_pack_idx == current_sticker_count:
                    print('add', emoji_pack_idx)
                    await bot.add_sticker_to_set(MY_UID, set_name, sticker)
                else:
                    file_id = sticker_set_info.stickers[emoji_pack_idx].file_id
                    print('update', emoji_pack_idx, file_id)
                    await bot.replace_sticker_in_set(
                        user_id=MY_UID, name=set_name,
                        old_sticker=file_id,
                        sticker=sticker
                    )
                current_sticker_count += 1
            i += 1
            if emoji_pack_idx == PACK_MAX_COUNT - 1:
                sticker_set_info = None
                current_sticker_count = 0

        # dymp emojies
        print('dumping ...')

        pack_id = 0
        i = 0
        all_count = len(data)
        while i < all_count:
            emojis = []
            set_name = f'{name}_{pack_id}_by_{me.username}'
            pack_info = await bot.get_sticker_set(set_name)
            print(f'pack {set_name} has {len(pack_info.stickers)}')
            local_idx = 0
            for sticker in pack_info.stickers:
                filename, key, emojistr, etype = data[i]
                emojis.append({
                    'index': local_idx,
                    'name': key,
                    'telegram_custom_emoji_id': str(sticker.custom_emoji_id),
                    'emoji': normalize_emoji(emojistr),
                })
                i += 1
                local_idx += 1
            pack_id += 1
            storage_payload = build_storage_payload(set_name, None, emojis)
            with open(f"storage/{set_name}.json", 'w', encoding='utf-8') as f:
                json.dump(storage_payload, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('usage: name path [start idx]')
        exit(0)

    name, path = sys.argv[1], sys.argv[2]
    if len(sys.argv) >= 4:
        start_idx = int(sys.argv[3])
    else:
        start_idx = 0

    p = Path(path)
    process_emojies(p)
    asyncio.run(upload_emojies(TOKEN, name, p, start_idx))
