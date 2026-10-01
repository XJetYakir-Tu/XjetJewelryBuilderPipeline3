"""Sign-in and self-service registration — the JewelryB2C2 flow (P2 main.py 986-1132).

Messages, status codes and register-once rules are kept identical to Pipeline 2. Mail goes
through p3.mail (an outbox by default), and links point at this deployment's base path.
"""

import html
import os

from fastapi import Request

from p3.accounts import AuthError
from p3.accounts.local import EmailPattern
from p3.context import Context, HttpError
from p3.mail import TokenEmail, VerificationEmail
from p3.settings import WebDir


def PublicOrigin(Request_: Request) -> str:
    """Origin used in emailed links. P3_PUBLIC_BASE_URL overrides it (P2: PUBLIC_BASE_URL),
    which is needed behind proto -> tron, where the request host is tron."""
    Base = os.environ.get("P3_PUBLIC_BASE_URL", "").strip().rstrip("/")
    return Base or f"{Request_.url.scheme}://{Request_.url.netloc}"


class RegistrationService:
    def __init__(self, Ctx: Context, Mailer):
        self.Ctx = Ctx
        self.Mailer = Mailer

    def _StudioUrl(self, Request_: Request) -> str:
        return f"{PublicOrigin(Request_)}{self.Ctx.Settings.BasePath}/"

    # POST /api/register  {Name, Email}
    def Register(self, Request_: Request, Name: str, Email: str) -> dict:
        Email = (Email or "").strip()
        if not EmailPattern.match(Email):
            raise HttpError(400, "invalid_email", "Please enter a valid email address.")
        R = self.Ctx.Accounts.StartEmailRegistration(Name, Email)
        if R["status"] == "already_registered":
            Subject, Body = TokenEmail(R["name"], R["token"], f"{self._StudioUrl(Request_)}#token={R['token']}")
            self.Mailer.Send(Email, Subject, Body)
            return {"status": "already_registered",
                    "message": "You're already registered — we've re-sent your access token to your email."}
        Link = f"{PublicOrigin(Request_)}{self.Ctx.Settings.BasePath}/verify?token={R['verify_secret']}"
        Subject, Body = VerificationEmail(R["name"], Link)
        self.Mailer.Send(Email, Subject, Body)
        return {"status": "verification_sent",
                "message": "We've sent a verification email to your inbox. Please verify your "
                           "email to save your designs and continue creating your jewelry."}

    # POST /api/register-token  {Token, Name, Email}  — "Already have a token? Enter it here"
    def SignInWithToken(self, Token: str) -> dict:
        try:
            Who = self.Ctx.Accounts.Authenticate(Token)
        except AuthError as E:
            if E.Code == "token_inactive":
                raise HttpError(403, "token_inactive", "This token has been deactivated. Contact XJet.") from E
            raise HttpError(404, "token_not_found", "Token not found. Check the code and try again.") from E
        P = self.Ctx.Accounts.Profile(Who)
        return {"ok": True, "used": P["used"], "max": P["max"], "remaining": P["remaining"], "name": P["name"]}

    # GET /verify?token=
    def VerifyPage(self, Request_: Request, Secret: str) -> str:
        R = self.Ctx.Accounts.VerifyEmail(Secret)
        Studio = self._StudioUrl(Request_)
        if R["status"] == "verified":
            Subject, Body = TokenEmail(R["name"], R["token"], f"{Studio}#token={R['token']}")
            self.Mailer.Send(R["email"], Subject, Body)
        return RenderVerifyPage(R["status"], R.get("token"), R["name"], Studio, self.Ctx.Settings.BasePath)


def RenderVerifyPage(Status: str, Token: str | None, Name: str, StudioUrl: str, BasePath: str) -> str:
    """P2 templates/verify.html, rendered without a template engine (all values escaped)."""
    E = lambda V: html.escape(V or "", quote=True)
    Ok = Status in ("verified", "already")
    if Status == "verified":
        Body = f"""
      <h1>Email verified</h1>
      <p>{E(Name) + ', your' if Name else 'Your'} email has been verified successfully.
        You're signed in — the button below takes you straight back to your design so you can
        continue creating your jewelry.</p>
      <a class="btn" href="{E(StudioUrl)}#token={E(Token)}">Continue designing →</a>
      <p style="margin-top:1.75rem;">Your personal access token (you only need it to sign in on
         another device — a copy is on its way to your inbox):</p>
      <div class="token">{E(Token)}</div>
      <p>Keep it safe — it's tied to your account and its generation quota.</p>"""
    elif Status == "already":
        # Tokens are stored hashed, so an already-used link cannot reveal the token again.
        Body = f"""
      <h1>Already verified</h1>
      <p>{E(Name) + ', your' if Name else 'Your'} email has already been verified. Your access token
        was sent to your inbox — use it to sign in, or register again with the same email to
        receive a new one.</p>
      <a class="btn" href="{E(StudioUrl)}">Continue designing →</a>"""
    elif Status == "expired":
        Body = f"""
      <h1>Link expired</h1>
      <p>This verification link has expired. Please register again from XJet Atelier to
         receive a fresh link.</p>
      <a class="btn" href="{E(BasePath)}/">Back to XJET ATELIER →</a>"""
    else:
        Body = f"""
      <h1>Invalid link</h1>
      <p>This verification link is not valid. It may have been mistyped or already used.
         Please register again from XJet Atelier.</p>
      <a class="btn" href="{E(BasePath)}/">Back to XJET ATELIER →</a>"""
    Page = (WebDir / "verify.html").read_text(encoding="utf-8")
    return Page.replace("{{CARD_CLASS}}", "" if Ok else "bad").replace("{{BODY}}", Body)
