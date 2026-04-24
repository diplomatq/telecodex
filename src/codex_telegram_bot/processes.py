from __future__ import annotations

import asyncio
import os
import signal


PROCESS_WAIT_TIMEOUT_SECONDS = 5.0


def subprocess_group_kwargs() -> dict[str, object]:
    if os.name == "nt":
        return {}
    return {"start_new_session": True}


async def terminate_process_tree(
    process: asyncio.subprocess.Process,
    *,
    wait_timeout: float = PROCESS_WAIT_TIMEOUT_SECONDS,
) -> None:
    if process.returncode is not None:
        return

    pid = getattr(process, "pid", None)
    if os.name != "nt" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            process.kill()
    elif process.returncode is None:
        process.kill()

    try:
        await asyncio.wait_for(process.wait(), timeout=wait_timeout)
    except asyncio.TimeoutError:
        if process.returncode is None:
            process.kill()
            await process.wait()
