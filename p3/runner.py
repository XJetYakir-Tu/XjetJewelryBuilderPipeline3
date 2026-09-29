"""Background task management and the shared provider polling contract.

One polling contract for images, movies and meshes (spec section 9):
  * bounded overall deadline per job kind;
  * transient read failures retry the SAME request, at most N consecutive times;
  * terminal states are explicit; nothing polls forever;
  * a key-based registry prevents two drivers for the same job.
"""

import asyncio
import logging
import time

from p3.providers.base import ClassifyErrorMessage, Provider, ProviderError, TransientProviderError

Logger = logging.getLogger("p3.runner")


class JobTimeout(Exception):
    pass


class TransientExhausted(Exception):
    pass


class TaskRunner:
    def __init__(self):
        self._Tasks: dict[str, asyncio.Task] = {}

    def Spawn(self, Key: str, Coro) -> bool:
        """Start Coro unless a live task already drives Key. Returns True if started."""
        Existing = self._Tasks.get(Key)
        if Existing and not Existing.done():
            Coro.close()
            return False
        Task = asyncio.get_running_loop().create_task(Coro, name=Key)
        self._Tasks[Key] = Task
        Task.add_done_callback(lambda T, K=Key: self._Done(K, T))
        return True

    def _Done(self, Key: str, Task: asyncio.Task) -> None:
        if self._Tasks.get(Key) is Task:
            del self._Tasks[Key]
        if not Task.cancelled() and Task.exception():
            Logger.error("Task %s crashed: %r", Key, Task.exception())

    def IsRunning(self, Key: str) -> bool:
        T = self._Tasks.get(Key)
        return bool(T and not T.done())

    async def WaitIdle(self, TimeoutS: float = 30.0) -> None:
        """Test helper: wait until every spawned task (including ones they spawn) finishes."""
        Deadline = time.monotonic() + TimeoutS
        while True:
            Pending = [T for T in self._Tasks.values() if not T.done()]
            if not Pending:
                return
            Remaining = Deadline - time.monotonic()
            if Remaining <= 0:
                raise TimeoutError(f"Tasks still running: {[T.get_name() for T in Pending]}")
            await asyncio.wait(Pending, timeout=Remaining)

    async def Shutdown(self) -> None:
        for T in list(self._Tasks.values()):
            T.cancel()
        await asyncio.gather(*self._Tasks.values(), return_exceptions=True)
        self._Tasks.clear()


async def PollUntilDone(ProviderObj: Provider, Endpoint: str, RequestId: str, TimeoutS: float,
                        IntervalS: float, MaxTransient: int) -> dict:
    """Poll one provider request to a terminal state and return its result payload."""
    Deadline = time.monotonic() + TimeoutS
    Transient = 0
    while True:
        if time.monotonic() > Deadline:
            raise JobTimeout(f"{Endpoint} request {RequestId} exceeded {TimeoutS:.0f}s")
        try:
            Status = await ProviderObj.Status(Endpoint, RequestId)
            Transient = 0
        except TransientProviderError as E:
            Transient += 1
            if Transient > MaxTransient:
                raise TransientExhausted(str(E)) from E
            await asyncio.sleep(IntervalS)
            continue
        if Status.State == "completed":
            if Status.Error:
                raise ProviderError(*ClassifyErrorMessage(Status.Error))
            return await _WithTransientRetry(lambda: ProviderObj.Result(Endpoint, RequestId),
                                             MaxTransient, IntervalS)
        await asyncio.sleep(IntervalS)


async def DownloadWithRetry(ProviderObj: Provider, Url: str, MaxTransient: int, IntervalS: float) -> bytes:
    return await _WithTransientRetry(lambda: ProviderObj.Download(Url), MaxTransient, IntervalS)


async def _WithTransientRetry(Call, MaxTransient: int, IntervalS: float):
    Attempts = 0
    while True:
        try:
            return await Call()
        except TransientProviderError as E:
            Attempts += 1
            if Attempts > MaxTransient:
                raise TransientExhausted(str(E)) from E
            await asyncio.sleep(IntervalS)


def FailureFor(E: Exception) -> tuple[str, str]:
    """(error message, error_code) for a job failure."""
    from p3.assets import AssetError
    if isinstance(E, ProviderError):
        return str(E), E.Code
    if isinstance(E, JobTimeout):
        return "The generation took too long and was stopped. You can retry.", "timeout"
    if isinstance(E, TransientExhausted):
        return "The generation service could not be reached. You can retry.", "provider_unreachable"
    if isinstance(E, TransientProviderError):
        return "Submitting to the generation service failed. You can retry.", "submit_failed"
    if isinstance(E, AssetError):
        return f"The generated file was invalid: {E}", "invalid_output"
    return f"Unexpected error: {E}", "internal_error"
