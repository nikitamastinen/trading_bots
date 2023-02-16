import asyncio
import sys
import time
from asyncio import CancelledError
from decimal import Decimal
from functools import wraps
from typing import Any, List, Dict

import binance.enums as be
from binance import BinanceSocketManager
from binance.client import AsyncClient
from binance.exceptions import BinanceAPIException

from utils.logger import Logger

from colorama import Fore, Back, Style


def add(a: str, b: str) -> str:
	return str(Decimal(a) + Decimal(b))


def sub(a: str, b: str) -> str:
	return str(Decimal(a) - Decimal(b))


def mul(a: str, b: int) -> str:
	return str(Decimal(a) * Decimal(b))


def less_or_eq(a: str, b: str) -> bool:
	return Decimal(a) <= Decimal(b)


def less(a: str, b: str) -> bool:
	return Decimal(a) < Decimal(b)


class KLine:
	def __init__(self, coin: str, base_currency: str, interval_tm: str, X: int = 80):
		self.coin: str = coin
		self.base_currency_ = base_currency
		self.opens: List[List[int, str]] = []
		self.X = X
		self.interval_tm = interval_tm

	async def init(self, client: AsyncClient):
		candles = await client.get_klines(symbol=self.coin + self.base_currency_, interval=self.interval_tm)
		for i in candles:
			self.opens.append([i[0], i[1], i[4]])
		self.opens.sort()
		self.opens = self.opens[-self.X:]

	def push(self, tin: int, cl: str, op: str):
		if len(self.opens) > 0 and tin == self.opens[-1][0]:
			return
		self.opens.append([tin, op, cl])
		self.opens = self.opens[1:]

	async def index(self) -> (float, float):
		while len(self.opens) == 0:
			await asyncio.sleep(self.sleep_delay)
		sma: float = 0
		for _, i, _ in self.opens:
			sma += float(i)
		return sma / self.X, float(self.opens[-3][1]), float(self.opens[-1][2])


def custom_round(num, dg=2):
	s = str(num)
	cnt = 0
	g = 0
	fl = False
	for i in s:
		g += 1
		if i == '.':
			fl = True
		if fl and i != '.':
			cnt += 1
		if cnt == dg:
			return s[0:g]
	return s


def repeatable(func):
	@wraps(func)
	async def wrapper(*args, **kwargs):

		for i in range(40):
			try:
				res = await func(*args, **kwargs)
				return res
			except BinanceAPIException as e:
				# logger.detailed_log('EXCEPTION', func.__name__, e, color=Fore.RED)
				print(e)
				await asyncio.sleep(0.5)
				continue
		raise RuntimeError

	return wrapper


class Grid:
	def __init__(self, key: str, secret: str, mutex: asyncio.Lock, leverage: int, logger: Logger):
		self.leverage: int = leverage  # change
		self.mutex = mutex

		self.key_: str = key
		self.secret_: str = secret

		self.rest_client_: AsyncClient | None = None
		self.ws_client_: BinanceSocketManager | None = None

		self.base_currency_: str = 'USDT'  # change
		self.coin_: str = 'BTC'  # change
		self.price_unit_: str = '0.01'

		self.price_: tuple[str, str] | None = None

		self.k_lines: KLine | None = None
		self.total_quantity = '0'
		self.is_long = True
		self.cor: asyncio.Task | None = None
		self.pitch: int = 0
		self.counter: int = 0
		self.filled_price: float | None = None

		self.sleep_delay = 0.1

		self.logger: Logger = logger

		self.coin_pool_value: str = '0'
		self.base_pool_value: str = '0'
		self.sum: float = 0
		self.S: float = 0.0006  # change
		self.interval_tf = '1s'  # change

	async def cancel_all_isolated_orders_(self):
		try:
			await self.rest_client_._request_margin_api(
				method='delete',
				path='margin/openOrders',
				signed=True,
				data={
					'symbol': f'{self.coin_}{self.base_currency_}',
					'isIsolated': 'TRUE',
				}
			)
		except BinanceAPIException as error:
			if error.code == -2011:
				return
			raise

	async def norm_target_pool(self, iterations=4):  # yellow
		await self.cancel_all_isolated_orders_()
		await self.logger.detailed_log('norm_target_pool:cancel', color=Fore.YELLOW)
		# --------------------------------- REPAY ----------------------------------------------------------------------

		for _ in range(3):
			positions = await self.rest_client_.get_isolated_margin_account(
				symbol=f'BTCUSDT')
			for i in positions['assets']:
				if i['symbol'] == f'{self.coin_}{self.base_currency_}':
					try:
						await self.rest_client_.repay_margin_loan(
							asset=self.coin_,
							symbol=f'{self.coin_}{self.base_currency_}',
							amount=custom_round(min(float(i['baseAsset']['borrowed']), float(i['baseAsset']['totalAsset'])), 5),
							isIsolated='TRUE',
						)
						await self.logger.detailed_log(f'norm_target_pool:repay_{self.coin_}', color=Fore.YELLOW)
					except Exception as e:
						await self.logger.detailed_log(f'norm_target_pool:exc_{self.coin_} {e}', color=Fore.YELLOW)

					try:
						await self.rest_client_.repay_margin_loan(
							asset=self.base_currency_,
							symbol=f'{self.coin_}{self.base_currency_}',
							amount=custom_round(min(float(i['quoteAsset']['borrowed']), float(i['quoteAsset']['totalAsset'])), 5),
							isIsolated='TRUE',
						)
						await self.logger.detailed_log(f'norm_target_pool:repay_{self.base_currency_}',
						                               color=Fore.YELLOW)
					except Exception as e:
						await self.logger.detailed_log(f'norm_target_pool:exc_{self.base_currency_} {e}',
						                               color=Fore.YELLOW)
					break
		# ----------------------------- NORM ---------------------------------------------------------------------------
		if iterations == 0:
			return
		try:
			async with self.mutex:
				await self.cancel_all_isolated_orders_()
				await self.logger.detailed_log('norm_target_pool:cancel', color=Fore.YELLOW)
				positions = await self.rest_client_.get_isolated_margin_account(
					symbol=f'{self.coin_}{self.base_currency_}')
				await self.logger.detailed_log('norm_target_pool:positions', color=Fore.YELLOW)
				for i in positions['assets']:
					if i['symbol'] == self.coin_ + self.base_currency_:
						debt: str = i['baseAsset']['free']
						is_b = False

						self.base_pool_value = i['quoteAsset']['borrowed']
						self.coin_pool_value = i['baseAsset']['borrowed']

						if less(debt, self.coin_pool_value):
							diff = sub(self.coin_pool_value, debt)
							is_b = True
						else:
							diff = sub(debt, self.coin_pool_value)
						if less(diff, '0.001'):
							# exit(0)
							return
						result = await self.rest_client_.create_margin_order(
							symbol=f'{self.coin_}{self.base_currency_}',
							side=be.SIDE_BUY if is_b else be.SIDE_SELL,
							type=be.ORDER_TYPE_MARKET,
							quantity=custom_round(diff, 5),
							isIsolated='TRUE'
						)
						await self.logger.detailed_log('norm_target_pool:order:', result, color=Fore.YELLOW)
						# exit(0)
						await self.norm_target_pool(iterations - 1)

		except BinanceAPIException as e:
			await self.logger.detailed_log('norm_target_pool:EXCEPTION:', e, color=Fore.YELLOW)
			await self.norm_target_pool(iterations - 1)

	async def position_change_handler(self, event):
		if event['e'] == 'executionReport' and event['X'] == 'FILLED' and event['o'] == 'LIMIT' and \
				event['s'] == self.coin_ + self.base_currency_:
			if 'sl' in event['c']:
				async with self.mutex:
					self.total_quantity = add(self.total_quantity, event['q'])
					await self.cancel_all_isolated_orders_()
					await self.logger.detailed_log('position_change_handler:cancel', color=Fore.MAGENTA)

					@repeatable
					async def close_order_tp1():
						result = await self.rest_client_.create_margin_order(
							symbol=f'{self.coin_}{self.base_currency_}',
							side=be.SIDE_SELL if self.is_long else be.SIDE_BUY,
							type=be.ORDER_TYPE_LIMIT,
							quantity=self.total_quantity,
							timeInForce=be.TIME_IN_FORCE_GTC,
							price=custom_round(float(event['p']) + self.pitch)
							if self.is_long else
							custom_round(float(event['p']) - self.pitch),
							newClientOrderId=f'tp{self.counter}',
							isIsolated='TRUE'
						)
						return result

					@repeatable
					async def lot2_order():
						result = await self.rest_client_.create_margin_order(
							symbol=f'{self.coin_}{self.base_currency_}',
							side=be.SIDE_BUY if self.is_long else be.SIDE_SELL,
							type=be.ORDER_TYPE_LIMIT,
							quantity=self.total_quantity,
							timeInForce=be.TIME_IN_FORCE_GTC,
							price=custom_round(self.filled_price - 2 * self.pitch) if self.is_long else
							custom_round(self.filled_price + 2 * self.pitch),
							newClientOrderId=f'sl2{self.counter}',
							isIsolated='TRUE'
						)

						return result

					print(event['c'])
					if event['c'] == f'sl1{self.counter}':
						await self.logger.detailed_log(
							'position_change_handler:lot1:enter', color=Fore.MAGENTA)
						await asyncio.gather(close_order_tp1(), lot2_order())
						await self.logger.detailed_log(
							'position_change_handler:open_order_lot1', color=Fore.MAGENTA)
						await self.logger.detailed_log(
							'position_change_handler:close_order_lot1', color=Fore.MAGENTA)
					else:
						result = await close_order_tp1()
						await self.logger.detailed_log(
							'position_change_handler:close_order_lot2', result, color=Fore.MAGENTA)

				asyncio.ensure_future(self.logger.log(
					f'{"long" if self.is_long else "short"} lot{event["c"][2:3]} {custom_round(event["p"])} {self.total_quantity}'
				))

			elif 'tp' in event['c']:
				if self.cor is not None:
					self.cor.cancel()
					asyncio.ensure_future(self.logger.log(
						f'{"long" if self.is_long else "short"} tp {event["p"]} {self.total_quantity}'
					))
					await self.logger.detailed_log('tp!')

	async def init_price_socket(self, coin: str):
		while True:
			try:
				price_socket = self.ws_client_.symbol_book_ticker_socket(f'{coin}{self.base_currency_}')

				async with price_socket:
					while True:
						res: Dict[str, Any] = await price_socket.recv()
						self.price_ = (res['a'], res['b'])
			except Exception as e:
				await self.logger.detailed_log('init_price_socket:exception:', e, color=Fore.RED)
				continue

	async def init_kline_socket(self, coin: str):
		while True:
			try:
				k_line_socket = self.ws_client_.kline_socket(f'{coin}{self.base_currency_}', interval=self.interval_tf)

				async with k_line_socket:
					while True:
						res: Dict[str, Any] = await k_line_socket.recv()
						op = res['k']['o']
						tin = res['k']['t']
						cl = res['k']['c']
						self.k_lines.push(op=op, cl=cl, tin=tin)
			except Exception as e:
				await self.logger.detailed_log('init_kline_socket:exception:', e, color=Fore.RED)
				continue

	async def init_trade_socket(self, coin: str):
		while True:
			try:
				trade_socket = self.ws_client_.isolated_margin_socket(symbol=f'{self.coin_}{self.base_currency_}')

				async with trade_socket:
					while True:
						res = await trade_socket.recv()
						await self.position_change_handler(res)

			except Exception as e:
				await self.logger.detailed_log('init_trade_socket:exception:', e, color=Fore.RED)
				continue

	async def init(self):
		cors = [
			self.init_price_socket(self.coin_),
			self.init_kline_socket(self.coin_),
			self.init_trade_socket(self.coin_)
		]
		await asyncio.gather(*cors)

	@repeatable
	async def get_current_balance(self):
		positions = await self.rest_client_.get_isolated_margin_account(
			symbol=f'{self.coin_}{self.base_currency_}')
		for i in positions['assets']:
			if i['symbol'] == f'{self.coin_}{self.base_currency_}':
				price = await self.rest_client_.get_avg_price(symbol=f'{self.coin_}{self.base_currency_}')
				balance = (float(i['baseAsset']['totalAsset']) - float(i['baseAsset']['borrowed'])) * float(
					price['price']) + (
						          float(i['quoteAsset']['totalAsset']) - float(i['quoteAsset']['borrowed']))
				print('balis', balance)
				return balance

	async def borrow(self):
		print('borrowing')
		amount = await self.get_current_balance()
		price_ = await self.rest_client_.get_avg_price(symbol=f'{self.coin_}{self.base_currency_}')
		price = price_['price']
		for i in range(4):
			try:
				if not self.is_long:
					await self.logger.detailed_log(f'borrow:creating {self.coin_} loan...', Fore.GREEN)

					result = await self.rest_client_.create_margin_loan(
						asset=f'{self.coin_}',
						symbol=f'{self.coin_}{self.base_currency_}',
						amount=custom_round(int(amount) * self.leverage * 0.94 / float(price), 5),
						isIsolated='TRUE'
					)
					print('RESULT', result)
					await self.logger.detailed_log(
						f'borrow:created short {self.coin_} {custom_round(int(amount) * self.leverage * 0.95 / float(price), 3)}',
						Fore.LIGHTGREEN_EX)

				if self.is_long:
					await self.logger.detailed_log(f'borrow:creating {self.base_currency_} loan...', Fore.GREEN)
					result = await self.rest_client_.create_margin_loan(
						asset=f'{self.base_currency_}',
						symbol=f'{self.coin_}{self.base_currency_}',
						amount=custom_round(int(amount) * self.leverage * 0.94, 5),
						isIsolated='TRUE'
					)
					print('RESULT', result)
					await self.logger.detailed_log(
						f'borrow:created long {self.base_currency_} {custom_round(int(amount) * self.leverage * 0.95, 3)}',
						Fore.GREEN)
				return True
			except BinanceAPIException as e:
				print(e)
				await asyncio.sleep(4 * (i + 1))
				continue
		return False

	@repeatable
	async def update_pools(self):
		await asyncio.sleep(0.5)
		positions = await self.rest_client_.get_isolated_margin_account(
			symbol=f'{self.coin_}{self.base_currency_}')
		await self.logger.detailed_log('update_pools:positions', color=Fore.MAGENTA)
		for i in positions['assets']:
			if i['symbol'] == self.coin_ + self.base_currency_:
				self.base_pool_value = i['quoteAsset']['borrowed']
				self.coin_pool_value = i['baseAsset']['borrowed']
				break

	async def iteration(self):
		async def cor():
			while True:
				self.total_quantity = '0'
				while self.price_ is None:
					await asyncio.sleep(self.sleep_delay)
				self.quantity = custom_round(self.sum / float(self.price_[1]) / 4, 5)

				index, o, c = await self.k_lines.index()
				if o > index > c:
					self.is_long = True
				elif o < index < c:
					self.is_long = False
				else:
					await asyncio.sleep(self.sleep_delay)
					continue

				r = await self.borrow()
				if not r:
					await asyncio.sleep(self.sleep_delay)
					await self.norm_target_pool()
					continue
				await self.update_pools()
				if self.is_long:
					self.quantity = custom_round(float(self.base_pool_value) / float(self.price_[1]) / 4.1, 5)
				else:
					self.quantity = custom_round(float(self.coin_pool_value) / 4.1, 5)

				print('starting iteration', self.quantity)
				await self.logger.detailed_log(self.quantity, color=Fore.BLUE)
				asyncio.ensure_future(self.logger.log(
					f'{"long" if self.is_long else "short"} enter'
				))

				@repeatable
				async def lot0_order():
					res = await self.rest_client_.create_margin_order(
						symbol=f'{self.coin_}{self.base_currency_}',
						side=be.SIDE_BUY if self.is_long else be.SIDE_SELL,
						type=be.ORDER_TYPE_MARKET,
						quantity=self.quantity,
						isIsolated='TRUE'
					)
					await self.logger.detailed_log('open_order_lot0:', res, color=Fore.BLUE)
					return res

				result = await lot0_order()
				self.total_quantity = add(self.quantity, self.total_quantity)
				self.filled_price = float(result['fills'][0]['price'])
				self.pitch = self.filled_price * self.S
				current_quantity = self.total_quantity

				asyncio.ensure_future(self.logger.log(
					f'{"long" if self.is_long else "short"} lot0 {custom_round(self.filled_price)} {self.total_quantity}'
				))

				@repeatable
				async def lot1_order():
					# print('one')
					resul = await self.rest_client_.create_margin_order(
						symbol=f'{self.coin_}{self.base_currency_}',
						side=be.SIDE_BUY if self.is_long else be.SIDE_SELL,
						type=be.ORDER_TYPE_LIMIT,
						quantity=current_quantity,
						timeInForce=be.TIME_IN_FORCE_GTC,
						price=custom_round(self.filled_price - self.pitch) if self.is_long else
						custom_round(self.filled_price + self.pitch),
						newClientOrderId=f'sl1{self.counter}',
						isIsolated='TRUE'
					)
					await self.logger.detailed_log('open_order_lot1:', resul, color=Fore.CYAN)

				@repeatable
				async def close_order_tp0():
					# print('three')
					resul = await self.rest_client_.create_margin_order(
						symbol=f'{self.coin_}{self.base_currency_}',
						side=be.SIDE_SELL if self.is_long else be.SIDE_BUY,
						type=be.ORDER_TYPE_LIMIT,
						quantity=current_quantity,
						timeInForce=be.TIME_IN_FORCE_GTC,
						price=custom_round(self.filled_price + self.pitch) if self.is_long
						else custom_round(self.filled_price - self.pitch),

						newClientOrderId=f'tp{self.counter}',
						isIsolated='TRUE'
					)
					await self.logger.detailed_log('close_order_lot1:', resul, color=Fore.CYAN)

				async with self.mutex:
					await asyncio.gather(
						lot1_order(),
						close_order_tp0()
					)
				while True:
					if self.is_long and float(self.price_[1]) <= self.filled_price - 3 * self.pitch \
							or not self.is_long and float(self.price_[0]) >= self.filled_price + 3 * self.pitch:
						async with self.mutex:
							await self.cancel_all_isolated_orders_()

							@repeatable
							async def close_position():
								res = await self.rest_client_.create_margin_order(
									symbol=f'{self.coin_}{self.base_currency_}',
									side=be.SIDE_SELL if self.is_long else be.SIDE_BUY,
									type=be.ORDER_TYPE_MARKET,
									quantity=self.total_quantity,
									isIsolated='TRUE'
								)
								await self.logger.detailed_log('close_order_sl_lot2:', res, color=Fore.BLUE)
								return res

							result = await close_position()
							r = float(result['fills'][0]['price'])

							asyncio.ensure_future(self.logger.log(
								f'{"long" if self.is_long else "short"} sl {custom_round(r)} {self.total_quantity}'
							))

							await self.logger.detailed_log('sl!')
							raise CancelledError

					await asyncio.sleep(self.sleep_delay)

		while True:
			await self.norm_target_pool()
			print('ok')
			self.counter += 1
			self.cor = asyncio.Task(cor())
			try:
				await self.cor
			except CancelledError as e:
				await self.cancel_all_isolated_orders_()
			except RuntimeError:
				await self.norm_target_pool()
				continue

	async def run(self):
		print("starting...")
		try:
			asyncio.ensure_future(self.logger.log('starting...'))

			self.rest_client_ = await AsyncClient.create(self.key_, self.secret_)
			self.ws_client_ = BinanceSocketManager(self.rest_client_)
			self.k_lines = KLine(self.coin_, self.base_currency_, interval_tm=self.interval_tf)
			await self.k_lines.init(self.rest_client_)
			await asyncio.gather(self.init(), self.iteration())
		except KeyboardInterrupt:
			print('stopped')
			await self.norm_target_pool()
			await self.rest_client_.close_connection()
			exit(0)
		except Exception as e:
			await self.logger.detailed_log('EXCEPTION:BAD', e, color=Fore.RED)
			await self.logger.log(f'undefined exception: {e}')
			raise RuntimeError
