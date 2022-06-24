import asyncio
import contextlib
import os
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from db import Database
from node.testnet2 import Testnet2
from node.type import u32, Transaction, Transition, Event


class Server(uvicorn.Server):
    # https://stackoverflow.com/a/64521239
    def install_signal_handlers(self):
        pass

    @contextlib.contextmanager
    def run_in_thread(self):
        thread = threading.Thread(target=self.run)
        thread.start()
        try:
            while not self.started:
                time.sleep(1e-3)
            yield
        finally:
            self.should_exit = True
            thread.join()


templates = Jinja2Templates(directory='webui/templates')


async def index_route(request: Request):
    recent_blocks = await db.get_recent_canonical_blocks_fast()
    data = []
    for block in recent_blocks:
        b = {
            "timestamp": block["timestamp"],
            "height": block["height"],
            "transactions": block["transaction_count"],
            "transitions": block["transition_count"],
            "owner": await db.get_miner_from_block_hash(block["block_hash"]),
            "block_hash": block["block_hash"],
        }
        data.append(b)
    ctx = {
        "latest_block": await db.get_latest_canonical_block_fast(),
        "request": request,
        "recent_blocks": data,
    }
    return templates.TemplateResponse('index.jinja2', ctx, headers={'Cache-Control': 'public, max-age=10'})


async def block_route(request: Request):
    height = request.query_params.get("h")
    block_hash = request.query_params.get("bh")
    if height is None and block_hash is None:
        raise HTTPException(status_code=400, detail="Missing height or block hash")
    if height is not None:
        block = await db.get_canonical_block_by_height(u32(int(height)))
        if block is None:
            raise HTTPException(status_code=404, detail="Block not found")
        is_canonical = True
    else:
        block = await db.get_block_by_hash(block_hash)
        if block is None:
            raise HTTPException(status_code=404, detail="Block not found")
        is_canonical = await db.is_block_hash_canonical(block.block_hash)
    latest_block_height = await db.get_latest_canonical_height()
    confirmations = latest_block_height - block.header.metadata.height + 1
    ledger_root = await db.get_ledger_root_from_block_hash(block.block_hash)

    testnet2_bug = False
    mining_reward = 0
    txs = []
    for tx in block.transactions.transactions:
        tx: Transaction
        if str(tx.ledger_root) == "al1gesxhq6vwwa2xx3uh8w5pcfsg7zh942c3tlqhdn5c8wt8lh5tvqqpu882x":
            testnet2_bug = True
        t = {
            "tx_id": tx.transaction_id,
            "transitions": len(tx.transitions),
        }
        balance = 0
        for ts in tx.transitions:
            ts: Transition
            balance += ts.value_balance.credit()
            if ts.value_balance < 0:
                mining_reward = ts.value_balance.credit()
        t["balance"] = balance
        txs.append(t)

    ctx = {
        "request": request,
        "block": block,
        "is_canonical": is_canonical,
        "confirmations": confirmations,
        "ledger_root": ledger_root,
        "owner": await db.get_miner_from_block_hash(block.block_hash),
        "testnet2_bug": testnet2_bug,
        "mining_reward": "%.6f" % -mining_reward,
        "transactions": txs,
    }
    return templates.TemplateResponse('block.jinja2', ctx, headers={'Cache-Control': 'public, max-age=30'})


async def transaction_route(request: Request):
    tx_id = request.query_params.get("id")
    if tx_id is None:
        raise HTTPException(status_code=400, detail="Missing transaction id")
    block = await db.get_best_block_from_transaction_id(tx_id)
    if block is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    is_canonical = await db.is_block_hash_canonical(block.block_hash)

    latest_block_height = await db.get_latest_canonical_height()
    confirmations = latest_block_height - block.header.metadata.height + 1

    transaction = None
    for tx in block.transactions.transactions:
        tx: Transaction
        if str(tx.transaction_id) == tx_id:
            transaction = tx
            break
    if transaction is None:
        raise HTTPException(status_code=550, detail="Transaction not found in block")

    ctx = {
        "request": request,
        "tx_id": tx_id,
        "tx_id_trunc": str(tx_id)[:9] + "..." + str(tx_id)[-6:],
        "block": block,
        "is_canonical": is_canonical,
        "confirmations": confirmations,
        "transaction": transaction,
    }
    return templates.TemplateResponse('transaction.jinja2', ctx, headers={'Cache-Control': 'public, max-age=30'})

async def transition_route(request: Request):
    ts_id = request.query_params.get("id")
    if ts_id is None:
        raise HTTPException(status_code=400, detail="Missing transition id")
    block = await db.get_best_block_from_transition_id(ts_id)
    if block is None:
        raise HTTPException(status_code=404, detail="Transition not found")

    transaction_id = None
    transition = None
    for tx in block.transactions.transactions:
        tx: Transaction
        for ts in tx.transitions:
            ts: Transition
            if str(ts.transition_id) == ts_id:
                transition = ts
                transaction_id = tx.transaction_id
                break
    if transaction_id is None:
        raise HTTPException(status_code=550, detail="Transition not found in block")

    public_record = [False, False]
    records = [None, None]
    is_dummy = [False, False]
    for event in transition.events:
        event: Event
        if event.type == Event.Type.RecordViewKey:
            public_record[event.event.index] = True
            record = await db.get_record_from_commitment(transition.commitments[event.event.index])
            if record.value == 0 and record.payload.is_empty() and record.program_id == Testnet2.noop_program_id:
                is_dummy[event.event.index] = True
            records[event.event.index] = record

    ctx = {
        "request": request,
        "ts_id": ts_id,
        "ts_id_trunc": str(ts_id)[:9] + "..." + str(ts_id)[-6:],
        "transaction_id": transaction_id,
        "transition": transition,
        "public_record": public_record,
        "records": records,
        "is_dummy": is_dummy,
    }
    return templates.TemplateResponse('transition.jinja2', ctx, headers={'Cache-Control': 'public, max-age=900'})


async def bad_request(request: Request, exc: HTTPException):
    return templates.TemplateResponse('400.jinja2', {'request': request, "exc": exc}, status_code=400)


async def not_found(request: Request, exc: HTTPException):
    return templates.TemplateResponse('404.jinja2', {'request': request, "exc": exc}, status_code=404)


async def internal_error(request: Request, exc: HTTPException):
    return templates.TemplateResponse('500.jinja2', {'request': request, "exc": exc}, status_code=500)


routes = [
    Route("/", index_route),
    Route("/block", block_route),
    Route("/transaction", transaction_route),
    Route("/transition", transition_route),
    # Route("/miner", miner_stats),
    # Route("/calc", calc),
]

exc_handlers = {
    400: bad_request,
    404: not_found,
    550: internal_error,
}

async def startup():
    async def noop(_): pass

    global db
    # different thread so need to get a new database instance
    db = Database(server=os.environ["DB_HOST"], user=os.environ["DB_USER"], password=os.environ["DB_PASS"],
                  database=os.environ["DB_DATABASE"], schema=os.environ["DB_SCHEMA"],
                  message_callback=noop)
    await db.connect()


# noinspection PyTypeChecker
app = Starlette(debug=True, routes=routes, on_startup=[startup], exception_handlers=exc_handlers)
db: Database


async def run():
    config = uvicorn.Config("webui:app", reload=True, log_level="info")
    server = Server(config=config)

    with server.run_in_thread():
        while True:
            await asyncio.sleep(3600)
