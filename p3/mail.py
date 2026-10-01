"""Registration emails (verification link, access token) — content mirrors P2 registration_mail.py.

Delivery is pluggable:
  * OutboxMailer (default, P3_MAIL_MODE=outbox): writes each message to var/outbox/ and never
    contacts a mail server — the right behaviour while P3 runs in mock mode. Developers read
    the messages (and click the verification links) from the /dev page.
  * SmtpMailer (P3_MAIL_MODE=smtp): the xjet3d relay, configured exactly like P2
    (SMTP_SERVER, SMTP_PORT, SMTP_PREFER_IPV4, MAIL_FROM, MAIL_FROM_NAME).
"""

import html
import json
import logging
import os
import smtplib
import socket
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

Logger = logging.getLogger("p3.mail")

_Style = ("font-family:Helvetica,Arial,sans-serif;color:#2A2A2A;line-height:1.6;"
          "max-width:520px;margin:0 auto;padding:32px 28px;background:#FAF8F4;border:1px solid #EDE8DF;")
_Button = ("background:#C9A96E;color:#1A1A1A;text-decoration:none;padding:14px 28px;font-size:12px;"
           "letter-spacing:2px;text-transform:uppercase;font-weight:700;")


def _Greeting(Name: str) -> str:
    Name = (Name or "").strip()
    return f"Hi {html.escape(Name)}," if Name else "Hello,"


def VerificationEmail(Name: str, VerifyUrl: str) -> tuple[str, str]:
    U = html.escape(VerifyUrl, quote=True)
    return ("Verify your email to continue designing — XJet Atelier", f"""\
<div style="{_Style}">
  <p style="text-transform:uppercase;letter-spacing:3px;font-size:11px;color:#C9A96E;font-weight:700;">XJet Atelier</p>
  <h2 style="font-weight:600;">One step from your design</h2>
  <p>{_Greeting(Name)}</p>
  <p><strong>Why you're receiving this:</strong> you registered with this email address at
     XJet Atelier while creating a piece of jewelry.</p>
  <p><strong>What happens next:</strong> verifying your email saves your designs to your
     account and signs you in. Clicking the button below takes you <em>straight back to your
     design</em> so you can keep creating &mdash; no extra login needed.</p>
  <p style="margin:28px 0;"><a href="{U}" style="{_Button}">Verify &amp; continue designing</a></p>
  <p style="font-size:13px;color:#666;">Or paste this link into your browser:<br><a href="{U}">{U}</a></p>
  <p style="font-size:13px;color:#666;">This link expires in 24&nbsp;hours. If you did
     not request this, you can safely ignore this email.</p>
</div>""")


def TokenEmail(Name: str, Token: str, LoginUrl: str) -> tuple[str, str]:
    U = html.escape(LoginUrl, quote=True)
    return ("Your XJet Atelier access token", f"""\
<div style="{_Style}">
  <p style="text-transform:uppercase;letter-spacing:3px;font-size:11px;color:#C9A96E;font-weight:700;">XJet Atelier</p>
  <h2 style="font-weight:600;">Your email is verified &mdash; keep designing</h2>
  <p>{_Greeting(Name)}</p>
  <p>Your email has been verified successfully. The button below takes you
     <em>straight back to your design</em>, already signed in, so you can continue
     creating your jewelry.</p>
  <p style="text-align:center;margin:28px 0;"><a href="{U}" style="{_Button}">Continue designing</a></p>
  <p style="font-size:13px;color:#666;">Your personal access token is below. You only
     need it to sign in on another device or browser &mdash; keep it safe, it is tied to
     your account and its generation quota.</p>
  <p style="margin:16px 0 28px;text-align:center;">
    <span style="display:inline-block;font-family:monospace;font-size:30px;letter-spacing:10px;font-weight:700;
                 color:#1A1A1A;border:2px solid #C9A96E;background:#FFFFFF;padding:16px 28px;">{html.escape(Token)}</span>
  </p>
</div>""")


class OutboxMailer:
    Mode = "outbox"

    def __init__(self, OutboxDir: Path):
        self.Dir = Path(OutboxDir)

    def Send(self, To: str, Subject: str, HtmlBody: str, Delivery: str = "outbox") -> str:
        self.Dir.mkdir(parents=True, exist_ok=True)
        Stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        Id = f"{Stamp}-{uuid.uuid4().hex[:8]}"
        (self.Dir / f"{Id}.json").write_text(json.dumps(
            {"id": Id, "to": To, "subject": Subject, "html": HtmlBody, "sent_at": Stamp, "delivery": Delivery},
            indent=2), encoding="utf-8")
        Logger.info("Outbox mail %s to %s: %s", Id, To, Subject)
        return Id

    def List(self, Limit: int = 50) -> list[dict]:
        if not self.Dir.is_dir():
            return []
        Files = sorted(self.Dir.glob("*.json"), reverse=True)[:Limit]
        return [json.loads(F.read_text(encoding="utf-8")) for F in Files]

    def Get(self, Id: str) -> dict | None:
        if not all(C.isalnum() or C in "-_" for C in Id):
            return None
        F = self.Dir / f"{Id}.json"
        return json.loads(F.read_text(encoding="utf-8")) if F.is_file() else None


class SmtpMailer:
    """P2 SendMailUtils.MailSender equivalent (xjet3d Exchange Online relay, no auth).

    Each sent message is also recorded in the developer outbox (var/outbox, readable only via
    the admin-key /api/dev/outbox) with the relay's verdict, so delivery can be diagnosed.
    """
    Mode = "smtp"

    def __init__(self, Record: "OutboxMailer | None" = None):
        self.Record = Record
        self.Server = os.environ.get("SMTP_SERVER", "xjet3d-com.mail.protection.outlook.com")
        self.Port = int(os.environ.get("SMTP_PORT", "25"))
        self.PreferIpv4 = os.environ.get("SMTP_PREFER_IPV4", "true").lower() in ("1", "true", "yes")
        self.From = os.environ.get("MAIL_FROM", "no-reply@xjet3d.com")
        self.FromName = os.environ.get("MAIL_FROM_NAME", "XJet Atelier")

    def Send(self, To: str, Subject: str, HtmlBody: str) -> str:
        Msg = EmailMessage()
        Msg["Subject"] = Subject
        Msg["From"] = formataddr((self.FromName, self.From))
        Msg["To"] = To
        Msg["Message-ID"] = make_msgid(domain=self.From.split("@")[-1])
        Msg["Reply-To"] = f"no-reply@{self.From.split('@', 1)[1]}"     # P2 no_reply=True
        Msg["Auto-Submitted"] = "auto-generated"
        Msg.set_content("This email requires an HTML-capable mail client.")
        Msg.add_alternative(HtmlBody, subtype="html")
        Host = socket.getaddrinfo(self.Server, None, socket.AF_INET)[0][4][0] if self.PreferIpv4 else self.Server
        try:
            with smtplib.SMTP(Host, self.Port, timeout=30) as Smtp:      # relay whitelists the IP: no auth/TLS
                Refused = Smtp.send_message(Msg)
        except Exception as E:
            if self.Record:
                self.Record.Send(To, Subject, HtmlBody, Delivery=f"smtp FAILED: {type(E).__name__}: {E}")
            raise
        Verdict = f"smtp refused: {Refused}" if Refused else f"smtp accepted by {self.Server}:{self.Port}"
        if self.Record:
            self.Record.Send(To, Subject, HtmlBody, Delivery=Verdict)
        Logger.info("SMTP mail to %s: %s — %s", To, Subject, Verdict)
        return Msg["Message-ID"]

    def List(self, Limit: int = 50) -> list[dict]:
        return self.Record.List(Limit) if self.Record else []

    def Get(self, Id: str) -> dict | None:
        return self.Record.Get(Id) if self.Record else None


def BuildMailer(DataDir: Path):
    Mode = os.environ.get("P3_MAIL_MODE", "outbox").strip().lower()
    if Mode == "smtp":
        return SmtpMailer(Record=OutboxMailer(Path(DataDir) / "outbox"))
    return OutboxMailer(Path(DataDir) / "outbox")
