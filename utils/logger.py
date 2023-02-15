import asyncio
from datetime import datetime
from time import time

from colorama import Fore, Style


class Logger:
	def __init__(self, path: str):
		self.soft_path: str = path + '/logs.out'
		self.detailed_path: str = path + '/output.out'

	async def log(self, *args, **kwargs):
		with open(self.soft_path, 'a+') as file:
			t = int(time())
			print(t, datetime.utcfromtimestamp(t), end=' ', file=file)
			print(t, datetime.utcfromtimestamp(t), end=' ')

			print(*args, **kwargs, file=file)
			print(*args, **kwargs)

	async def detailed_log(self, *args, color: Fore = Fore.RESET, **kwargs):
		with open(self.detailed_path, "a+") as file:
			print(color, end='')
			print(color, end='', file=file)

			print(*args, **kwargs)
			print(*args, **kwargs, file=file)

			print(Style.RESET_ALL, end='')
			print(Style.RESET_ALL, end='', file=file)
