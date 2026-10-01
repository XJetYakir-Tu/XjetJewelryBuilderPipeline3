"""fal.ai queue adapter (live provider)."""

import httpx
import fal_client

from p3.providers.base import ProviderError, ProviderStatus, TransientProviderError, ClassifyErrorMessage

_TransientHttp = {408, 425, 429, 500, 502, 503, 504}
MaxDownloadBytes = 512 * 1024 * 1024


def _Wrap(E: Exception) -> Exception:
    if isinstance(E, (httpx.TransportError, fal_client.FalClientTimeoutError)):
        return TransientProviderError(str(E))
    if isinstance(E, fal_client.FalClientHTTPError):
        if "downstream_service_error" in str(E) or "Downstream service error" in str(E):
            # fal.ai reports that the model's own (partner) service failed on this request. It is a
            # failed generation, not a connection problem, so it is not retried as transient.
            return ProviderError("The AI service failed to generate this result (fal.ai: downstream service error). "
                                 "You can retry; if it keeps failing, check the model settings.", "provider_failed")
        if E.status_code in _TransientHttp:
            return TransientProviderError(f"HTTP {E.status_code}: {E}")
        Message, Code = ClassifyErrorMessage(f"HTTP {E.status_code}: {E}")
        return ProviderError(Message, Code)
    Message, Code = ClassifyErrorMessage(str(E))
    return ProviderError(Message, Code)


class FalProvider:
    Name = "fal"

    def __init__(self, Key: str):
        self.Client = fal_client.AsyncClient(key=Key)

    def Owns(self, RequestId: str) -> bool:
        return not RequestId.startswith("mockreq_")

    async def Upload(self, Data: bytes, ContentType: str) -> str:
        try:
            return await self.Client.upload(Data, ContentType)
        except Exception as E:
            raise _Wrap(E) from E

    async def Submit(self, Endpoint: str, Arguments: dict) -> str:
        try:
            Handle = await self.Client.submit(Endpoint, Arguments)
            return Handle.request_id
        except Exception as E:
            raise _Wrap(E) from E

    async def Status(self, Endpoint: str, RequestId: str) -> ProviderStatus:
        try:
            S = await self.Client.status(Endpoint, RequestId)
        except Exception as E:
            raise _Wrap(E) from E
        if isinstance(S, fal_client.Queued):
            return ProviderStatus("queued")
        if isinstance(S, fal_client.InProgress):
            return ProviderStatus("running")
        if isinstance(S, fal_client.Completed):
            return ProviderStatus("completed", getattr(S, "error", None))
        return ProviderStatus("running")

    async def Result(self, Endpoint: str, RequestId: str) -> dict:
        try:
            return await self.Client.result(Endpoint, RequestId)
        except Exception as E:
            raise _Wrap(E) from E

    async def Download(self, Url: str) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as Client:
                async with Client.stream("GET", Url) as Resp:
                    if Resp.status_code in _TransientHttp:
                        raise TransientProviderError(f"Download HTTP {Resp.status_code}")
                    if Resp.status_code != 200:
                        raise ProviderError(f"Download failed: HTTP {Resp.status_code}", "download_failed")
                    Chunks, Size = [], 0
                    async for Chunk in Resp.aiter_bytes(65536):
                        Size += len(Chunk)
                        if Size > MaxDownloadBytes:
                            raise ProviderError("Downloaded artifact is too large", "download_failed")
                        Chunks.append(Chunk)
                    return b"".join(Chunks)
        except httpx.TransportError as E:
            raise TransientProviderError(str(E)) from E
