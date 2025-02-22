from __future__ import annotations

import os
from asyncio import iscoroutinefunction
from typing import Awaitable, ParamSpec

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from aleo_types import *
from explorer.types import Message as ExplorerMessage

try:
    from line_profiler import profile
except ImportError:
    P = ParamSpec('P')
    R = TypeVar('R')
    def profile(func: Callable[P, Awaitable[R]] | Callable[P, R]) -> Callable[P, Awaitable[R]] | Callable[P, R]:
        if iscoroutinefunction(func):
            async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                return await func(*args, **kwargs)
            return wrapper
        else:
            func = cast(Callable[P, R], func)
            def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                return func(*args, **kwargs)
            return wrapper

class DatabaseBase:

    def __init__(self, *, server: str, user: str, password: str, database: str, schema: str, redis_server: str,
                 redis_port: int, redis_db: int, redis_user: Optional[str], redis_password: Optional[str],
                 message_callback: Callable[[ExplorerMessage], Awaitable[None]]):
        self.server = server
        self.user = user
        self.password = password
        self.database = database
        self.schema = schema
        self.message_callback = message_callback
        self.redis_server = redis_server
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.redis_user = redis_user
        self.redis_password = redis_password

        self.pool: AsyncConnectionPool
        self.redis: Redis[str]

    async def connect(self):
        try:
            self.pool = AsyncConnectionPool(
                f"host={self.server} user={self.user} password={self.password} dbname={self.database} "
                f"options=-csearch_path={self.schema} application_name=aleo-explorer-{os.environ.get('NETWORK', 'unknown')}",
                kwargs={
                    "row_factory": dict_row,
                    "autocommit": True,
                },
                max_size=16,
            )
            # noinspection PyArgumentList
            self.redis = Redis(host=self.redis_server, port=self.redis_port, db=self.redis_db, decode_responses=True,
                               username=self.redis_user, password=self.redis_password)
        except Exception as e:
            await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseConnectError, e))
            return
        await self.message_callback(ExplorerMessage(ExplorerMessage.Type.DatabaseConnected, None))

