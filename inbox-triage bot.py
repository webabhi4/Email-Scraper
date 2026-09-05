import os
import json
import base64
import re

from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"

GEMINI_MODEL = "gemini-3.7-flash"

MAX_EMAILS = 15

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# ============================================================
# GMAIL AUTHENTICATION
# ============================================================

def authenticate_gmail():
    """
    Authenticate the user with Gmail using OAuth.
    """

    creds = None

    # Load previously saved token
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            SCOPES
        )

    # Refresh or create new credentials
    if not creds or not creds.valid:

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    "credentials.json not found. "
                    "Download it from Google Cloud Console "
                    "and place it in this folder."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE,
                SCOPES
            )

            creds = flow.run_local_server(port=0)

        # Save token for future runs
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ============================================================
# EMAIL BODY EXTRACTION
# ============================================================

def decode_base64(data):
    """
    Decode Gmail's Base64 encoded email content.
    """

    try:
        return base64.urlsafe_b64decode(data).decode(
            "utf-8",
            errors="ignore"
        )
    except Exception:
        return ""


def extract_email_body(payload):
    """
    Extract readable text from an email payload.
    """

    # Simple email
    if "body" in payload and payload["body"].get("data"):
        return decode_base64(payload["body"]["data"])

    # Multipart email
    if "parts" in payload:

        text_parts = []

        for part in payload["parts"]:

            mime_type = part.get("mimeType", "")

            if mime_type == "text/plain":

                body_data = part.get("body", {}).get("data")

                if body_data:
                    text_parts.append(
                        decode_base64(body_data)
                    )

            elif "parts" in part:

                nested_text = extract_email_body(part)

                if nested_text:
                    text_parts.append(nested_text)

        return "\n".join(text_parts)

    return ""


# ============================================================
# GET EMAIL HEADER
# ============================================================

def get_header(headers, name):
    """
    Get a specific email header.
    """

    for header in headers:

        if header["name"].lower() == name.lower():
            return header["value"]

    return ""


# ============================================================
# FETCH UNREAD EMAILS
# ============================================================

def fetch_unread_emails(gmail_service):
    """
    Fetch unread emails from the Gmail inbox.
    """

    results = gmail_service.users().messages().list(
        userId="me",
        labelIds=["INBOX", "UNREAD"],
        maxResults=MAX_EMAILS
    ).execute()

    messages = results.get("messages", [])

    emails = []

    for message in messages:

        msg = gmail_service.users().messages().get(
            userId="me",
            id=message["id"],
            format="full"
        ).execute()

        payload = msg.get("payload", {})

        headers = payload.get("headers", [])

        sender = get_header(headers, "From")
        subject = get_header(headers, "Subject")
        date = get_header(headers, "Date")

        body = extract_email_body(payload)

        emails.append({
            "id": message["id"],
            "sender": sender,
            "subject": subject,
            "date": date,
            "body": body
        })

    return emails


# ============================================================
# GEMINI ANALYSIS
# ============================================================

def analyze_email_with_gemini(email):
    """
    Send email information to Gemini and receive
    category, urgency and draft reply information.
    """

    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY environment variable is not set."
        )

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
You are an email triage assistant.

Analyze the following email.

Email sender:
{email['sender']}

Email subject:
{email['subject']}

Email body:
{email['body']}

Classify the email into exactly ONE of these categories:

- needs_reply
- fyi
- newsletter
- action_item
- spam_or_promo

Also give an urgency score from 1 to 5.

Urgency meaning:

1 = Very low urgency
2 = Low urgency
3 = Medium urgency
4 = High urgency
5 = Extremely urgent

Determine whether the email should receive a draft reply.

Only create a draft reply when a reply is genuinely appropriate
and useful.

Return ONLY valid JSON in this exact format:

{{
    "category": "needs_reply",
    "urgency": 3,
    "reasoning": "Short explanation",
    "should_draft_reply": true,
    "draft_reply": "Reply text"
}}

If no reply is needed, use:

"should_draft_reply": false

and:

"draft_reply": ""

Do not send any email.
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt
    )

    response_text = response.text.strip()

    # Remove Markdown JSON fences if Gemini adds them
    response_text = re.sub(
        r"^```json\s*",
        "",
        response_text,
        flags=re.IGNORECASE
    )

    response_text = re.sub(
        r"\s*```$",
        "",
        response_text
    )

    result = json.loads(response_text)

    # Basic validation
    allowed_categories = {
        "needs_reply",
        "fyi",
        "newsletter",
        "action_item",
        "spam_or_promo"
    }

    if result.get("category") not in allowed_categories:
        result["category"] = "fyi"

    try:
        result["urgency"] = int(result.get("urgency", 1))
    except (TypeError, ValueError):
        result["urgency"] = 1

    result["urgency"] = max(
        1,
        min(5, result["urgency"])
    )

    result["should_draft_reply"] = bool(
        result.get("should_draft_reply", False)
    )

    result["draft_reply"] = result.get(
        "draft_reply",
        ""
    )

    result["reasoning"] = result.get(
        "reasoning",
        ""
    )

    return result


# ============================================================
# EMAIL ADDRESS EXTRACTION
# ============================================================

def extract_email_address(sender):
    """
    Extract the actual email address from a From header.
    """

    match = re.search(
        r"<([^<>@\s]+@[^<>@\s]+)>",
        sender
    )

    if match:
        return match.group(1)

    match = re.search(
        r"[\w.+-]+@[\w.-]+\.\w+",
        sender
    )

    if match:
        return match.group(0)

    return sender


# ============================================================
# CREATE GMAIL DRAFT
# ============================================================

def create_gmail_draft(gmail_service, email, draft_text):
    """
    Create a Gmail draft.
    This function NEVER sends the email.
    """

    recipient = extract_email_address(
        email["sender"]
    )

    subject = email["subject"]

    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    message = MIMEText(draft_text)

    message["to"] = recipient
    message["subject"] = subject

    raw_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    body = {
        "message": {
            "raw": raw_message
        }
    }

    draft = gmail_service.users().drafts().create(
        userId="me",
        body=body
    ).execute()

    return draft


# ============================================================
# PRINT EMAIL RESULT
# ============================================================

def print_email_result(email, analysis):
    """
    Print one analyzed email to the terminal.
    """

    print("\n" + "=" * 70)

    print(f"From      : {email['sender']}")
    print(f"Subject   : {email['subject']}")
    print(f"Category  : {analysis['category']}")
    print(f"Urgency   : {analysis['urgency']}/5")
    print(f"Reason    : {analysis['reasoning']}")

    if analysis["should_draft_reply"]:
        print("Draft     : YES")
    else:
        print("Draft     : NO")


# ============================================================
# MAIN PROGRAM
# ============================================================

def main():

    print("=" * 70)
    print("             INBOX TRIAGE BOT")
    print("=" * 70)

    print("\nAuthenticating with Gmail...")

    try:
        gmail_service = authenticate_gmail()

    except Exception as error:
        print(f"\nGmail authentication failed:")
        print(error)
        return

    print("Gmail authentication successful.")

    print("\nFetching unread emails...")

    try:
        emails = fetch_unread_emails(
            gmail_service
        )

    except Exception as error:
        print(f"\nCould not fetch emails:")
        print(error)
        return

    if not emails:
        print("\nNo unread emails found.")
        return

    print(
        f"Found {len(emails)} unread email(s)."
    )

    print("\nAnalyzing emails with Gemini...")

    analyzed_emails = []

    for email in emails:

        try:

            analysis = analyze_email_with_gemini(
                email
            )

            email_result = {
                **email,
                **analysis
            }

            analyzed_emails.append(
                email_result
            )

            print_email_result(
                email,
                analysis
            )

            # Create draft if Gemini recommends it
            if analysis["should_draft_reply"]:

                draft = create_gmail_draft(
                    gmail_service,
                    email,
                    analysis["draft_reply"]
                )

                print(
                    f"Draft created successfully "
                    f"(Draft ID: {draft.get('id')})"
                )

        except Exception as error:

            print(
                f"\nError processing email "
                f"'{email['subject']}':"
            )

            print(error)

    # ========================================================
    # SORT BY URGENCY
    # ========================================================

    analyzed_emails.sort(
        key=lambda x: x["urgency"],
        reverse=True
    )

    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    print("\n\n")
    print("=" * 70)
    print("                    FINAL SUMMARY")
    print("=" * 70)

    for index, email in enumerate(
        analyzed_emails,
        start=1
    ):

        print(
            f"\n{index}. "
            f"[Urgency {email['urgency']}/5] "
            f"{email['subject']}"
        )

        print(
            f"   Category: {email['category']}"
        )

        print(
            f"   From: {email['sender']}"
        )

    print("\n" + "=" * 70)
    print("Inbox triage completed.")
    print("No emails were sent automatically.")
    print("=" * 70)


# ============================================================
# PROGRAM START
# ============================================================

if __name__ == "__main__":
    main()