import httpx
import asyncio

async def findtaffy():
    client = httpx.AsyncClient()
    for i in range(1001, 2001):
        print('trying ', i)
        try:
            resp = await client.get('https://api.bilibili.com/x/emote/package',
                    params={'ids': str(i), 'business': 'reply'},
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'}
                    )
            if '1265680561' in resp.text:
                print(resp.text)
                return
            print(i, resp.json()['data']['packages'][0]['text'])
        except Exception as e:
            print('error', i, e)
        await asyncio.sleep(0.5)


if __name__ == '__main__':
    asyncio.run(findtaffy())
