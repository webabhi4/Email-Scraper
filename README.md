# Inbox Triage Bot

Sorts your unread Gmail by urgency and drafts easy replies for you —
never sends anything automatically. Free to run: uses the Gmail API
and Google's free Gemini API tier.

## Getting started

```
git clone https://github.com/your-username/inbox-triage-bot.git
cd inbox-triage-bot
pip install -r requirements.txt
```

## One-time setup

### 1. Gmail API access
1. Go to [console.cloud.google.com](https://console.cloud.google.com), create a new project.
2. Enable the **Gmail API** (APIs & Services > Library).
3. Set up the **OAuth consent screen** (External, add yourself as a test user).
4. Create **OAuth credentials** (APIs & Services > Credentials > Create Credentials > OAuth client ID > Desktop app).
5. Download the credentials file, rename it to `credentials.json`, and put it in this same folder as `inbox_triage.py`.

### 2. Gemini API key (free)
1. Go to [aistudio.google.com](https://aistudio.google.com).
2. Sign in, click "Get API key", create a new key.
3. Set it as an environment variable before running the script:
   - Mac/Linux: `export GEMINI_API_KEY=your_key_here`
   - Windows (PowerShell): `$env:GEMINI_API_KEY='your_key_here'`

   Check current free-tier limits at [ai.google.dev/pricing](https://ai.google.dev/pricing) —
   also double-check the model name in `inbox_triage.py` (`GEMINI_MODEL`)
   is still current, since Google updates their Flash model lineup periodically.

## Running it

```
python inbox_triage.py
```

- First run: a browser window opens asking you to approve Gmail access. Approve it.
- After that, it reuses a saved `token.json` — no need to log in again.
- It fetches your unread emails, sends each one to Gemini for labeling/scoring,
  saves draft replies for the easy ones, and prints a sorted summary like:

```
🔴 Urgent (needs you today)
   - sarah@company.com: "Contract sign-off needed" (deadline mentioned)

🟡 Should look at soon
   - mike@friend.com: "Free for a call this week?" (casual, no deadline)

⚪ Low priority / no action needed
   - newsletter@service.com: "This week's digest" (automated newsletter)
```

## Notes

- **Nothing is ever sent automatically.** Draft replies are saved to your
  Gmail Drafts folder for you to review, edit, and send yourself.
- `MAX_EMAILS` in the script controls how many unread emails are processed
  per run (default 15) — raise or lower it as needed.
- `credentials.json` and `token.json` contain sensitive access — don't
  share them or commit them to a public repo. The included `.gitignore`
  already excludes both, along with `.env`, so a normal `git add .` won't
  accidentally commit your secrets.
