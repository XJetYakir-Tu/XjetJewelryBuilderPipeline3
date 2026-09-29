"""Wiring of the shared services used by every module (no globals)."""

import asyncio
from dataclasses import dataclass, field

from p3.config import Catalog, GenerationConfig
from p3.db import Database
from p3.pricing.service import PricingService
from p3.providers.base import Provider
from p3.runner import TaskRunner
from p3.settings import Settings


class HttpError(Exception):
    """Service-level error carrying an HTTP status and a stable error code."""

    def __init__(self, Status: int, Code: str, Message: str):
        super().__init__(Message)
        self.Status = Status
        self.Code = Code
        self.Message = Message


@dataclass
class Context:
    Settings: Settings
    Db: Database
    Provider: Provider
    Gen: GenerationConfig
    Catalog: Catalog
    Pricing: PricingService
    Runner: TaskRunner = field(default_factory=TaskRunner)
    ImageSemaphore: asyncio.Semaphore | None = None
    _Locks: dict = field(default_factory=dict)

    def Lock(self, Key: str) -> asyncio.Lock:
        if Key not in self._Locks:
            self._Locks[Key] = asyncio.Lock()
        return self._Locks[Key]

    def Semaphore(self) -> asyncio.Semaphore:
        if self.ImageSemaphore is None:
            self.ImageSemaphore = asyncio.Semaphore(self.Gen.Images.MaxConcurrentRequests)
        return self.ImageSemaphore

    def AssetUrl(self, RelPath: str | None) -> str | None:
        return f"/assets/{RelPath}" if RelPath else None
