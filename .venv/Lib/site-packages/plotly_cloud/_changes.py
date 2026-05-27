import asyncio
import hashlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor

_builtins_modules = list(sys.builtin_module_names) + ["frozen", "builtin"]


if hasattr(sys, "stdlib_module_names"):
    _builtins_modules += list(sys.stdlib_module_names)  # type: ignore


def collect_module_files(*extras):
    collected = []
    for mod in sys.modules.values():
        parent = getattr(mod.__spec__, "parent", "")
        if (
            hasattr(mod, "__file__")
            and mod.__file__ is not None
            and mod.__file__.endswith(".py")
            and parent not in _builtins_modules
            and mod.__name__ not in _builtins_modules
        ):
            collected.append(mod.__file__)
    return collected + list(extras)


def hash_file_content(filename):
    with open(filename, encoding="utf-8") as f:
        content = f.read()
        return hashlib.sha256(content.encode("utf-8"), usedforsecurity=False).hexdigest(), filename


def stat_file_time(filename):
    return os.stat(filename).st_mtime, filename


async def until_change(collector, *extras, initial_hashes=None):
    # We hash ten files at a time.
    pool = ThreadPoolExecutor(max_workers=10)
    loop = asyncio.get_event_loop()

    changed = False
    hashed_files = {}

    async def collect():
        collected = []
        files = collector(*extras)
        for f in files:
            collected.append(loop.run_in_executor(pool, stat_file_time, f))

        content = await asyncio.gather(*collected)
        result = {}

        for hashed, filename in content:
            result[filename] = hashed

        return result

    if initial_hashes is None or not hashed_files:
        hashed_files = await collect()

    while not changed:
        collected = await collect()

        if len(collected) != len(hashed_files):
            return

        for co_key, co_value in collected.items():
            if co_key not in hashed_files:
                return

            if co_value != hashed_files[co_key]:
                return

        await asyncio.sleep(1.0)
