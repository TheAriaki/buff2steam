import asyncio
import re
import time

import httpx

import buff2steam.exceptions
from buff2steam import logger


class Steam:
    base_url = 'https://steamcommunity.com'

    web_sell = '/market/sellitem'
    web_inventory = '/inventory/{steam_id}/{game_appid}/{context_id}'
    web_listings = '/market/listings/{game_appid}/{market_hash_name}'
    web_listings_render = web_listings + '/render'
    steam_order_api = '/market/itemordershistogram'

    item_nameid_pattern = re.compile(r'Market_LoadOrderSpread\(\s*(\d+)\s*\)')
    wanted_cnt_pattern = re.compile(r'<span\s*class="market_commodity_orders_header_promote">(\d+)</span>')

    def __init__(
            self, asf_config=None, steam_id=None, game_appid='', context_id=2,
            request_interval=30, request_kwargs=None
    ):
        self.request_interval = request_interval
        self.request_locks = {}  # {url: [asyncio.Lock, last_request_time]}
        self.opener = httpx.AsyncClient(base_url=self.base_url, **request_kwargs)
        self.asf_config = asf_config
        self.game_appid = game_appid
        self.context_id = context_id
        self.web_inventory = self.web_inventory.format(
            steam_id=steam_id,
            game_appid=game_appid,
            context_id=context_id
        )
        self.web_listings = self.web_listings.replace('{game_appid}', game_appid)
        self.web_listings_render = self.web_listings_render.replace('{game_appid}', game_appid)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.opener.aclose()

    async def request(self, *args, **kwargs):
        url = kwargs.get('url', args[1])
        if url not in self.request_locks:
            self.request_locks[url] = [asyncio.Lock(), 0]

        async with self.request_locks[url][0]:
            elapsed = time.monotonic() - self.request_locks[url][1]
            if elapsed < self.request_interval:
                logger.debug(f'Waiting {self.request_interval - elapsed:.2f} seconds before next request({url})...')
                await asyncio.sleep(self.request_interval - elapsed)
            self.request_locks[url][1] = time.monotonic()

            return await self.opener.request(*args, **kwargs)

    async def listings_data(self, market_hash_name):
        res = await self.request('GET', self.web_listings_render.format(market_hash_name=market_hash_name), params={
            'count': 1,
            'currency': 23
        })

        if res.status_code == 429:
            raise buff2steam.exceptions.SteamAPI429Error()

        res = res.json()

        listinginfo = res['listinginfo'][next(iter(res['listinginfo']))]
        converted_price = listinginfo['converted_price']
        converted_fee = listinginfo['converted_fee']

        return {
            'converted_price': converted_price,
            'total_count': res['total_count'],
            'steam_tax_ratio': converted_price / (converted_price + converted_fee)
        }

    async def orders_data(self, market_hash_name):
        res = await self.request('GET', self.web_listings.format(market_hash_name=market_hash_name))

        item_nameid = self.item_nameid_pattern.findall(res.text)[0]

        res = await self.request('GET', self.steam_order_api, params={
            'language': 'schinese',
            'currency': 23,
            'item_nameid': item_nameid,
        })

        orders_data = res.json()

        return {
            'highest_buy_order': orders_data['highest_buy_order'],
            'wanted_cnt': self.wanted_cnt_pattern.findall(orders_data['buy_order_summary'])[0]
        }
