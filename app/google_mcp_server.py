"""MCP server exposing Gmail and Google Calendar as tools (stdio transport).

Deliberately conservative: no tool in this server sends or deletes email, so the
agent cannot email anyone without a human pressing send. Note that Google has no
draft-only OAuth scope: ``gmail.compose`` is the narrowest scope that allows
creating drafts, and it technically also permits sending. The guarantee comes
from the tool surface, not the scope.

Run standalone:  python -m app.google_mcp_server
"""

from __future__ import annotations

import base64
import os
from email.message import EmailMessage

from mcp.server.fastmcp import FastMCP

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar",
]

mcp = FastMCP("google-workspace")


def _creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_file = os.environ.get("GOOGLE_TOKEN_FILE", "secrets/token.json")
    creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_file, "w") as f:
            f.write(creds.to_json())
    return creds


def _service(name: str, version: str):
    from googleapiclient.discovery import build

    return build(name, version, credentials=_creds(), cache_discovery=False)


def _clamp(n: int, low: int = 1, high: int = 50) -> int:
    return max(low, min(high, int(n)))


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _body_text(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _body_text(part)
        if text:
            return text
    return ""


@mcp.tool()
def calendar_list_events(time_min: str, time_max: str, max_results: int = 20) -> list[dict]:
    """List calendar events between two RFC3339 timestamps (e.g. 2026-10-26T00:00:00+05:30)."""
    svc = _service("calendar", "v3")
    res = (
        svc.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=_clamp(max_results),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return [
        {
            "id": e["id"],
            "summary": e.get("summary", "(no title)"),
            "start": e["start"].get("dateTime", e["start"].get("date")),
            "end": e["end"].get("dateTime", e["end"].get("date")),
            "location": e.get("location", ""),
        }
        for e in res.get("items", [])
    ]


@mcp.tool()
def calendar_create_event(
    summary: str, start: str, end: str, timezone: str = "Asia/Kolkata", description: str = ""
) -> dict:
    """Create a calendar event. start/end are RFC3339 timestamps."""
    svc = _service("calendar", "v3")
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
    }
    ev = svc.events().insert(calendarId="primary", body=body).execute()
    return {"id": ev["id"], "link": ev.get("htmlLink", "")}


@mcp.tool()
def gmail_search(query: str, max_results: int = 10) -> list[dict]:
    """Search Gmail using Gmail query syntax (e.g. 'from:alice is:unread newer_than:2d')."""
    svc = _service("gmail", "v1")
    ids = (
        svc.users().messages().list(userId="me", q=query, maxResults=_clamp(max_results)).execute()
    )
    out = []
    for m in ids.get("messages", []):
        full = (
            svc.users()
            .messages()
            .get(
                userId="me",
                id=m["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            .execute()
        )
        out.append(
            {
                "id": m["id"],
                "from": _header(full, "From"),
                "subject": _header(full, "Subject"),
                "date": _header(full, "Date"),
                "snippet": full.get("snippet", ""),
            }
        )
    return out


@mcp.tool()
def gmail_read(message_id: str) -> dict:
    """Read the plain-text body of one email by id (truncated to 4000 chars)."""
    svc = _service("gmail", "v1")
    full = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    return {
        "from": _header(full, "From"),
        "to": _header(full, "To"),
        "subject": _header(full, "Subject"),
        "date": _header(full, "Date"),
        "body": _body_text(full["payload"])[:4000],
    }


@mcp.tool()
def gmail_create_draft(to: str, subject: str, body: str) -> dict:
    """Create a Gmail DRAFT (never sends). The user reviews and sends it themselves."""
    svc = _service("gmail", "v1")
    msg = EmailMessage()
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    d = svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return {"draft_id": d["id"], "note": "Draft saved in Gmail. Not sent."}


if __name__ == "__main__":
    mcp.run()
