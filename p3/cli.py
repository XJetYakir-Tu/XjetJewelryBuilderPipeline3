"""Operator CLI (acts on Pipeline 3's own database only).

  python -m p3.cli create-token --label "QA tester"
  python -m p3.cli list-tokens
  python -m p3.cli deactivate-token p3_xxx
  python -m p3.cli check-provider     # verify FAL_KEY with a free storage upload (no generation)
"""

import argparse
import asyncio
import io

from p3.auth import CreateToken
from p3.db import Database
from p3.settings import LoadSettings


class _Ctx:
    def __init__(self):
        self.Settings = LoadSettings()
        self.Db = Database(self.Settings.DbPath)


def Main(Argv=None) -> int:
    Parser = argparse.ArgumentParser(prog="p3.cli")
    Sub = Parser.add_subparsers(dest="cmd", required=True)
    Create = Sub.add_parser("create-token"); Create.add_argument("--label", default="")
    Sub.add_parser("list-tokens")
    Deact = Sub.add_parser("deactivate-token"); Deact.add_argument("token")
    Sub.add_parser("check-provider")
    Args = Parser.parse_args(Argv)
    if Args.cmd == "check-provider":
        return CheckProvider()
    Ctx = _Ctx()
    print(f"# database: {Ctx.Settings.DbPath}")
    if Args.cmd == "create-token":
        print(CreateToken(Ctx, Args.label))
    elif Args.cmd == "list-tokens":
        for R in Ctx.Db.All("SELECT token, label, active, created_at FROM access_tokens ORDER BY created_at"):
            print(f"{R['token']}\t{'active' if R['active'] else 'inactive'}\t{R['created_at']}\t{R['label']}")
    elif Args.cmd == "deactivate-token":
        N = Ctx.Db.Execute("UPDATE access_tokens SET active = 0 WHERE token = ?", (Args.token,))
        print("deactivated" if N else "not found")
    return 0


def CheckProvider() -> int:
    """Confirm the configured provider is reachable and the key is accepted.

    Live mode uploads a 1x1 PNG to fal storage, which exercises authentication
    without running (or paying for) any model.
    """
    try:
        S = LoadSettings()
    except ValueError as E:
        print(f"Configuration error: {E}")
        return 2
    if S.Provider == "mock":
        print("Provider: MOCK - no AI provider is called. Set P3_PROVIDER=fal and FAL_KEY for live mode.")
        return 0
    from PIL import Image
    from p3.providers.fal import FalProvider
    Buf = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 255, 255)).save(Buf, format="PNG")
    try:
        Url = asyncio.run(FalProvider(S.FalKey).Upload(Buf.getvalue(), "image/png"))
    except Exception as E:
        print(f"Provider: LIVE (fal) - key check FAILED: {E}")
        return 1
    print(f"Provider: LIVE (fal) - key accepted (test upload: {Url}). No model was run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())
