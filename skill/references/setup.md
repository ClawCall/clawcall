# ClawCall — Backend & Agent Setup Reference

## Environment Variables

| Variable | Description |
|---|---|
| `CLAWCALL_API_KEY` | UUID issued on registration. Store securely — never shown again. |
| `CLAWCALL_EMAIL` | Auto-generated email tied to your account. Store for account recovery. |

---

## How Calls Reach Your Agent

ClawCall uses a **pull model** — your agent polls an endpoint to receive
incoming call messages. No public URL, no inbound webhook, no Tailscale required.

### Step 1 — Poll for incoming messages

```
GET https://api.clawcall.online/api/v1/calls/listen?timeout=25
Authorization: Bearer {CLAWCALL_API_KEY}
```

**When a call is waiting:**
```json
{ "ok": true, "call_sid": "CA...", "message": "User's transcribed speech" }
```

**When no call arrived within timeout:**
```json
{ "ok": true, "timeout": true }
```

### Step 2 — Submit your response

```
POST https://api.clawcall.online/api/v1/calls/respond/{call_sid}
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{ "response": "Agent's reply text — spoken via TTS", "end_call": false }
```

Set `end_call: true` to hang up after speaking.

---

## Message Prefixes

| Prefix | Meaning |
|---|---|
| *(none)* | Normal inbound call from user |
| `[SCHEDULED] <context>` | Scheduled call — deliver the briefing |
| `[THIRD PARTY CALL]` | Opening of an autonomous 3rd party call — start the conversation |
| `[THIRD PARTY SAYS]: <speech>` | Third party's spoken response — continue the conversation |
| `[THIRD PARTY COMPLETE]` | Third-party call ended — JSON transcript follows in the message body |

---

## Registration

```
POST https://api.clawcall.online/api/v1/register
Content-Type: application/json

{
  "phone_number": "<user's E.164 phone, e.g. +14155550100>",
  "agent_name":   "<optional display name>"
}
```

No `agent_webhook_url` is required or accepted — the polling model
replaces inbound webhooks entirely.

**Response:**
```json
{
  "ok": true,
  "api_key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "email": "agent-abc123@clawcall.app",
  "phone_number": "+18447954032",
  "tier": "free"
}
```

Store `api_key` as `CLAWCALL_API_KEY` and `email` as `CLAWCALL_EMAIL`.
The email is used only for account recovery (re-registration).

---

## Number Types

| Tier | Number Type | Caller ID |
|---|---|---|
| Free | Shared pool | "ClawCall" |
| Pro | Dedicated | Your number |
| Team | Dedicated (×5) | Your number |

Free tier identifies callers by their registered phone number (`from_number`).
Only calls from the registered number are accepted on free tier.

---

## Voices

Set via `POST /api/v1/account/voice`:

| Shortname | Voice | Accent |
|---|---|---|
| `aria` | Aria Neural | US English, Female (default) |
| `joanna` | Joanna Neural | US English, Female |
| `matthew` | Matthew Neural | US English, Male |
| `amy` | Amy Neural | British English, Female |
| `brian` | Brian Neural | British English, Male |
| `emma` | Emma Neural | British English, Female |
| `olivia` | Olivia Neural | Australian English, Female |

---

## Overage Billing

Pro/Team users are never hard-blocked past their included minutes.
Overage accrues at **$0.05/minute** and is added to the next renewal payment.
Check current overage via `GET /api/v1/billing/status`.

---

## Recording Access

Call recordings are stored as `.mp3` files hosted by Twilio.
Retrieve URLs via `GET /api/v1/calls/history?transcripts=true` — see the
`recording_url` field on each call log.

---

## Error Codes

| Code | Meaning |
|---|---|
| 400 | Missing required field |
| 401 | Missing or invalid CLAWCALL_API_KEY |
| 403 | Feature not available on your tier |
| 404 | Resource not found |
| 409 | Conflict (e.g. transaction already used) |
| 429 | Monthly minute limit reached |
| 500 | Internal error |
