"""Bounded, account-scoped instrument metadata cache. Never stores quotes or credentials."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from time import monotonic
from threading import RLock


class InstrumentCatalogCache:
    def __init__(self, ttl=120, maximum=128, clock=monotonic):
        self.ttl=ttl; self.maximum=maximum; self.clock=clock
        self.entries=OrderedDict(); self.pending={}; self.lock=RLock()

    async def get(self, key, loader):
        with self.lock:
            cached=self.entries.get(key)
            if cached and cached[0] > self.clock():
                self.entries.move_to_end(key)
                return deepcopy(cached[1])
        task_key=(asyncio.get_running_loop(), key)
        task=self.pending.get(task_key)
        if task is None:
            async def load():
                result=await loader()
                if isinstance(result,(dict,list)) and not (isinstance(result,dict) and result.get("status")=="not_implemented"):
                    with self.lock:
                        self.entries[key]=(self.clock()+self.ttl,deepcopy(result))
                        self.entries.move_to_end(key)
                        while len(self.entries)>self.maximum: self.entries.popitem(last=False)
                return result
            task=asyncio.create_task(load()); self.pending[task_key]=task
        try: return deepcopy(await task)
        finally:
            if task.done() and self.pending.get(task_key) is task: self.pending.pop(task_key,None)

    def invalidate_user(self, user):
        with self.lock:
            for key in list(self.entries):
                if key[0] == user: self.entries.pop(key,None)


instrument_catalog_cache=InstrumentCatalogCache()
