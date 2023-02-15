import asyncio
import importlib
import json
import os
import platform
import time
from logging import Logger
import argparse
from utils.logger import Logger

if platform.system() == 'Windows':
	asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def start(bot_name: str, user_name: str, leverage: str) -> None:
	bot = importlib.import_module(f'bots.{bot_name}')

	with open(f'privates/{user_name}.json') as file:
		params = json.load(file)
		key = params['API_KEY']
		secret = params['API_SECRET']

	if not os.path.exists(f'logs/{user_name}'):
		os.makedirs(f'logs/{user_name}')

	logger = Logger(path=f'logs/{user_name}')

	while True:
		try:
			mutex = asyncio.Lock()
			bot_ = bot.Grid(key=key, secret=secret, mutex=mutex, logger=logger, leverage=int(leverage))

			async def run():
				await asyncio.gather(
					bot_.run(),
				)

			asyncio.run(run())
			break
		except BaseException as e:
			time.sleep(1)
			print(e)
			continue


if __name__ == '__main__':
	parser = argparse.ArgumentParser(description='Choose bot to run')

	parser.add_argument('bot_name', type=str, help='Bot directory name')
	parser.add_argument('user_name', type=str, help='User name')
	parser.add_argument('leverage', type=str, help='Leverage to trade')

	args = parser.parse_args()

	print(args.__dict__)
	start(
		bot_name=args.__dict__['bot_name'],
		user_name=args.__dict__['user_name'],
		leverage=args.__dict__['leverage']
	)
