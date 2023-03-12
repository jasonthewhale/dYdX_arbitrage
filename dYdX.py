from discord_webhook import DiscordWebhook
from dydx_constants import *
from rage_constants import *
from dydx3 import Client
from web3 import Web3
import requests
import asyncio
import dotenv
import time
import os

# Set up env
dotenv.load_dotenv('.env')

# Constants
WALLET=os.getenv('DEFAULT_ADDR')
PK=os.getenv('PK')

class Discord():
    def __init__(self):
        pass

    def send_message(self, word):
        webhook = DiscordWebhook(url=os.getenv('WEBHOOK'), content='@everyone ' + word)
        response = webhook.execute()
        return response

class DYDX():
    def __init__(self):
        w3 = Web3(Web3.HTTPProvider(os.getenv('MAINNET_RPC')))
        self.client = Client(
            network_id=NETWORK_ID_MAINNET,
            api_key_credentials={
                'passphrase':os.getenv('PASSPHRASE'),
                'key':os.getenv('KEY'),
                'secret':os.getenv('SECRET')
            },
            stark_private_key=os.getenv('STARK_PK'),
            host=API_HOST_MAINNET,
            default_ethereum_address=os.getenv('DEFAULT_ADDR'),
            web3=w3
        )
        self.account = self.client.private.get_account()
        self.position_id = self.account.data['account']['positionId']

    def get_price_data(self, market_symbol):
        market = self.client.public.get_markets(market=market_symbol)
        data = market.data["markets"][market_symbol]
        oracle_price = data["oraclePrice"]
        index_price = data["indexPrice"]
        next_funding = data["nextFundingRate"]
        price_data = {'index_price': index_price, 'oracle_price': oracle_price, 'next_funding': next_funding}
        return price_data
    
    def get_account_data(self):
        account_response = self.client.private.get_account()
        account = account_response.data['account']
        # position_id = account['positionId']
        equity = account['equity']
        free_collateral = account['freeCollateral']
        open_position = account.get('openPositions')
        if not open_position:
            account_data = {'balance': equity, 'collateral': free_collateral, 'position': 0}
            return account_data
        else:
            account_data = {'balance': equity, 'collateral': free_collateral, 'position': open_position.get(MARKET_ETH_USD).get('size')}
            return account_data
    
    async def place_market_order(self, market_symbol, side, size):
        index_price = float(self.get_price_data(market_symbol)['index_price'])
        limit_price = str(int(index_price + 5 * (1 if side == "BUY" else -1)))
        order_params = {
        'position_id': self.position_id,
        'market': market_symbol,
        'side': side,
        'price': limit_price,
        'order_type': 'MARKET',
        'size': size,
        'limit_fee': '0.0015',
        'post_only': False,
        'time_in_force':'FOK',
        'expiration_epoch_seconds': time.time() + 66,
        }
        order_response = self.client.private.create_order(**order_params)
        order_id = order_response.data['order']['id']
        print(f'dYdX order sent, order id is: {order_id}')
        await asyncio.sleep(10)
        order = self.client.private.get_order_by_id(order_id)
        status_of_order = order.data['order']['status']
        account_data = self.get_account_data()
        if status_of_order == 'FILLED':
            text = f'Order filled on DYDX, size is: {size}. Current account data is: {account_data}'
            Discord().send_message(text)
            print(text)
        else:
            text = f"Order wasn't filled on DYDX, size is: {size}. Current account data is: {account_data}"
            Discord().send_message(text)
            print(text)

    def is_filled(self, order_id):
        order = self.client.private.get_order_by_id(order_id)
        status_of_order = order.data['order']['status']
        if status_of_order == 'FILLED':
            return True
        return False
    
    def get_order_book(self, market_symbol):
        order_book = self.client.public.get_orderbook(market=market_symbol)
        return order_book


class Rage():
    def __init__(self):
        w3 = Web3(Web3.HTTPProvider(os.getenv('ARBITRUM_RPC')))
        self.w3 = w3
        self.clearing_house = w3.eth.contract(address=MAINNET_CLEARING_HOUSE_ADDR, abi=CLEARING_HOUSE_ABI)
        self.simulate = w3.eth.contract(address=MAINNET_SIMULATOR_ADDR, abi=SIMULATOR_ABI)
        self.pool_id = 2721558366
        self.account_id = 10491

    def get_sqrtprice(self, price):
        return int((price ** 0.5) * (2 ** 96) / 1e6)
    
    def get_perp_price(self):
        num = self.clearing_house.functions.getVirtualTwapPriceX128(self.pool_id).call()
        return num / 2**128 * 1e12
    
    def get_token_position(self):
        position = self.clearing_house.functions.getAccountNetTokenPosition(
            self.account_id,
            self.pool_id
        ).call()
        return position / 1e18
    
    async def place_order(self, price_limit, size):
        original_position = self.get_token_position()
        amount = self.w3.toWei(abs(size), 'ether') * (1 if size >= 0 else -1)
        price_limit = self.get_sqrtprice(price_limit)
        swap_params = {
            'amount': amount,
            'sqrtPriceLimit': price_limit,
            'isNotional': False,
            'isPartialAllowed': False,
            'settleProfit': True
        }
        swap = self.clearing_house.functions.swapToken(
            self.account_id,
            self.pool_id,
            swap_params
        )
        nonce = self.w3.eth.getTransactionCount(WALLET)
        tx_params = {
            'chainId': 421613,
            'value':  0,
            'gasPrice': self.w3.toWei(0.1, 'gwei'),
            'gas': 1877644,
            'nonce': nonce
	    }
        try:
            tx = swap.buildTransaction(tx_params)
            signed_tx = self.w3.eth.account.sign_transaction(tx, os.getenv('PK'))
            tx_hash = self.w3.eth.sendRawTransaction(signed_tx.rawTransaction)
            txn = tx_hash.hex()
            print(f'Transaction sent on Rage: {txn}')
        except Exception as e:
            print(f'{WALLET} Transaction Failed: ', e)
        await asyncio.sleep(10)
        current_position = self.get_token_position()
        if abs(current_position - original_position) >= 0.01:
            text = f'Order placed on Rage, size is: {size}. Current position is: {current_position}'
            Discord().send_message(text)
            print(text)
        else:
            text = f"Order wasn't placed on Rage, size is: {size}. Current position is: {current_position}"
            Discord().send_message(text)
            print(text)
    
    def is_filled(self, original_position):
        current_position = self.get_token_position()
        if abs(current_position - original_position) >= 0.01:
            return True
        return False

    def simulate_swap(self, size): 
        amount = self.w3.toWei(abs(size), 'ether') * (size > 0) - self.w3.toWei(abs(size), 'ether') * (size < 0)
        swap_simulation = self.simulate.functions.simulateSwapView(
            MAINNET_CLEARING_HOUSE_ADDR,
            self.pool_id,
            amount,
            0,
            False
        ).call()
        simulation_price = swap_simulation[2] / 1e6
        price_per_token = simulation_price / size
        return price_per_token


async def main():
    dydx = DYDX()
    rage = Rage()
    discord = Discord()
    while 1:
        try:
            dydx_position = abs(float(dydx.get_account_data()['position']))
        except requests.exceptions.ConnectionError as e:
            discord.send_message('Connection error', e)
            await asyncio.sleep(5)
            return
        rage_position = abs(rage.get_token_position())
        if abs(dydx_position - rage_position) >= 0.1 or dydx_position >= 5 or rage_position >= 5:
            print('Need manual adjust position')
            discord.send_message('Need manual adjust position')
            break
        dydx_price = float(dydx.get_price_data(MARKET_ETH_USD)['index_price'])
        rage_mark_price = rage.get_perp_price()
        price_dif = rage_mark_price - dydx_price
        print(f'----------------------------------------------------------------------------\
              \nmonitoring, price diff is: {price_dif}')
        if abs(price_dif) >= 15:
            print('Possible arbitrage opportunity appeared!!!\n')
            discord.send_message('Possible arbitrage opportunity appeared!!!')
            if price_dif < 0:
                rage_long_size_list = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
                rage_long_available_size_list = [l for l in rage_long_size_list if rage.simulate_swap(l) - float(dydx.get_price_data(MARKET_ETH_USD)['index_price']) <= -15]
                if rage_long_available_size_list:
                    biggest_avaiable_size = max(rage_long_available_size_list)
                    rage_price = rage.simulate_swap(biggest_avaiable_size)
                    await asyncio.gather(rage.place_order(rage_price + 5, biggest_avaiable_size),
                                        dydx.place_market_order(MARKET_ETH_USD, 'SELL', str(biggest_avaiable_size)))
                    discord.send_message(f'***Tried long on Rage and short on dYdX, size is: {biggest_avaiable_size}***')
            if price_dif > 0:
                rage_short_size_list = [-0.5, -0.6, -0.7, -0.8, -0.9, -1.0]
                rage_short_available_size_list = [s for s in rage_short_size_list if rage.simulate_swap(s) - float(dydx.get_price_data(MARKET_ETH_USD)['index_price']) >= 15]
                if rage_short_available_size_list:
                    biggest_avaiable_size = min(rage_short_available_size_list)
                    rage_price = rage.simulate_swap(biggest_avaiable_size)
                    await asyncio.gather(rage.place_order(rage_price - 5, biggest_avaiable_size),
                            dydx.place_market_order(MARKET_ETH_USD, 'BUY', str(abs(biggest_avaiable_size))))
                    discord.send_message(f'***Tried short on Rage and long on dYdX, size is: {biggest_avaiable_size}***')
        await asyncio.sleep(60)

asyncio.run(main())
