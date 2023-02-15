#!/usr/bin/env python3
import argparse
import json
from _decimal import Decimal

import binance
import binance.enums as be
from binance.exceptions import BinanceAPIException


def less(a: str, b: str) -> bool:
	return Decimal(a) < Decimal(b)


def sub(a: str, b: str) -> str:
	return str(Decimal(a) - Decimal(b))


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


def run():
	parser = argparse.ArgumentParser(description='Choose bot to run')
	parser.add_argument('bot_dir', type=str, help='Bot directory name')
	# parser.add_argument('amount', type=str, help='Amount USDT to loan')
	args = parser.parse_args()
	bot_dir_name = args.__dict__["bot_dir"]
	# amount = args.__dict__['amount']

	# assert ('alf' not in bot_dir_name and 'alex' not in bot_dir_name and 'nik' not in bot_dir_name)
	# --------------------------------------------------

	with open(f'privates/{bot_dir_name}.json') as file:
		params = json.load(file)
		key = params['API_KEY']
		secret = params['API_SECRET']

	client = binance.Client(api_key=key, api_secret=secret)
	positions = client.get_isolated_margin_account(
		symbol=f'BTCUSDT')
	print(positions)
	for i in positions['assets']:
		if i['symbol'] == 'BTCUSDT':
			print(json.dumps(i['baseAsset'], indent=2))
			print(json.dumps(i['quoteAsset'], indent=2))

			price = client.get_avg_price(symbol='BTCUSDT')
			print(price)
			# print(float(i['baseAsset']['totalAsset']) - float(i['baseAsset']['borrowed']))
			balance = (float(i['baseAsset']['totalAsset']) - float(i['baseAsset']['borrowed'])) * float(price['price']) + (
						float(i['quoteAsset']['totalAsset']) - float(i['quoteAsset']['borrowed']))
			print('Current balance is', balance)
			break


run()
