"""
Inbox Triage Bot
-----------------
Reads your unread Gmail messages, uses Gemini (free API) to label and
score each one by urgency, drafts replies for the easy ones (drafts only,
never sends), and prints a prioritized summary to your terminal.

Run manually with:
    python inbox_triage.py

Requires:
    - credentials.json  (Gmail OAuth client, downloaded from Google Cloud Console)
    - GEMINI_API_KEY     (environment variable, from Google AI Studio)

First run will open a browser window asking you to approve Gmail access.
After that, a token.json file is saved so you won't have to log in again.
"""

import os
import json
import base64
import re
from email.mime.text import MIMEText

import google.generativeai as genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

# Gmail API scopes:
#   gmail.readonly -> read your unread mail
#   gmail.compose   -> create drafts (does NOT allow sending)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"

# How many unread emails to pull and process per run
MAX_EMAILS = 15

# Which Gemini model to use. "gemini-2.0-flash" is the current free-tier
# workhorse model as of early 2026 -- check https://ai.google.dev/pricing
# for the latest free-tier model name/limits before relying on this.
GEMINI_MODEL = "gemini-2.0-flash"


# ----------------------------------------------------------------------
# GMAIL AUTH
# ----------------------------------------------------------------------

def get_gmail_service():
    """Handles OAuth login and returns an authenticated Gmail API client."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Couldn't find {CREDENTIALS_FILE}. Download it from "
                    "Google Cloud Console (APIs & Services > Credentials) "
                    "and place it in this folder."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ----------------------------------------------------------------------
# FETCH UNREAD EMAILS
# ----------------------------------------------------------------------

def get_unread_emails(service, max_results=MAX_EMAILS):
    """Returns a list of dicts: {id, sender, subject, snippet}."""
    results = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["UNREAD", "INBOX"], maxResults=max_results)
        .execute()
    )
    messages = results.get("messages", [])

    emails = []
    for msg_meta in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_meta["id"], format="metadata",
                 metadataHeaders=["From", "Subject"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        emails.append({
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "sender": headers.get("From", "Unknown sender"),
            "subject": headers.get("Subject", "(no subject)"),
            "snippet": msg.get("snippet", ""),
        })
    return emails


# ----------------------------------------------------------------------
# GEMINI: LABEL, SCORE, DRAFT
# ----------------------------------------------------------------------

def analyze_email(model, email):
    """
    Asks Gemini to classify the email, score its urgency, and (if it's an
    easy one) write a short draft reply. Returns a dict.
    """
    prompt = f"""You are an email triage assistant. Analyze this email and
respond with ONLY a JSON object, no other text, no markdown formatting.

From: {email['sender']}
Subject: {email['subject']}
Preview: {email['snippet']}

Return JSON with exactly these fields:
{{
  "category": one of ["needs_reply", "fyi", "newsletter", "action_item", "spam_or_promo"],
  "urgency": an integer from 1 (not urgent) to 5 (very urgent),
  "reasoning": a very short (under 15 words) reason for the urgency score,
  "should_draft_reply": true or false (true only if a short, easy reply makes sense),
  "draft_reply": a short draft reply text if should_draft_reply is true, else an empty string
}}"""

    response = model.generate_content(prompt)
    text = response.text.strip()

    # Strip markdown code fences if Gemini adds them anyway
    text = re.sub(r"^```json\s*|\s*```$", "", text.strip())

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {
            "category": "fyi",
            "urgency": 1,
            "reasoning": "Could not parse model response",
            "should_draft_reply": False,
            "draft_reply": "",
        }
    return result


# ----------------------------------------------------------------------
# CREATE GMAIL DRAFT
# ----------------------------------------------------------------------

def create_draft(service, to_address, subject, body, thread_id):
    """Creates a draft reply. Does NOT send anything."""
    message = MIMEText(body)
    message["to"] = to_address
    message["subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    draft_body = {
        "message": {
            "raw": raw,
            "threadId": thread_id,
        }
    }
    service.users().drafts().create(userId="me", body=draft_body).execute()


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "Set the GEMINI_API_KEY environment variable before running.\n"
            "  Mac/Linux: export GEMINI_API_KEY=your_key_here\n"
            "  Windows (PowerShell): $env:GEMINI_API_KEY='your_key_here'"
        )
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    print("Connecting to Gmail...")
    service = get_gmail_service()

    print("Fetching unread emails...")
    emails = get_unread_emails(service)

    if not emails:
        print("No unread emails found. You're all caught up!")
        return

    print(f"Analyzing {len(emails)} emails with Gemini...\n")

    analyzed = []
    for email in emails:
        result = analyze_email(model, email)
        email.update(result)
        analyzed.append(email)

        if email.get("should_draft_reply") and email.get("draft_reply"):
            try:
                sender_address = re.search(r"<(.+?)>", email["sender"])
                to_addr = sender_address.group(1) if sender_address else email["sender"]
                create_draft(
                    service,
                    to_addr,
                    email["subject"],
                    email["draft_reply"],
                    email["thread_id"],
                )
                email["draft_created"] = True
            except Exception as e:
                email["draft_created"] = False
                email["draft_error"] = str(e)

    # Sort by urgency, highest first
    analyzed.sort(key=lambda e: e["urgency"], reverse=True)

    # ------------------------------------------------------------------
    # PRINT SUMMARY
    # ------------------------------------------------------------------
    print("=" * 60)
    print("INBOX TRIAGE SUMMARY")
    print("=" * 60)

    buckets = {"high": [], "medium": [], "low": []}
    for e in analyzed:
        if e["urgency"] >= 4:
            buckets["high"].append(e)
        elif e["urgency"] >= 2:
            buckets["medium"].append(e)
        else:
            buckets["low"].append(e)

    labels = {
        "high": "🔴 Urgent (needs you today)",
        "medium": "🟡 Should look at soon",
        "low": "⚪ Low priority / no action needed",
    }

    for key in ["high", "medium", "low"]:
        if not buckets[key]:
            continue
        print(f"\n{labels[key]}")
        for e in buckets[key]:
            draft_note = ""
            if e.get("draft_created"):
                draft_note = "  [draft reply saved]"
            elif e.get("should_draft_reply"):
                draft_note = "  [draft failed, see error]"
            print(f"   - {e['sender']}: \"{e['subject']}\" ({e['reasoning']}){draft_note}")

    print("\nDone. Check your Gmail Drafts folder for any drafted replies.")


if __name__ == "__main__":
    main()
