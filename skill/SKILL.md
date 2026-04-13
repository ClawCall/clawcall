---
name: clawcall
version: 2.1.0
description: >
  Give this agent a real phone number. Receive calls from the user,
  call user back when tasks complete, run scheduled calls, or call
  third parties on the user's behalf. No public URL required.
metadata:
  openclaw:
    requires:
      bins: ["node", "openclaw"]
      env:
        - CLAWCALL_API_KEY
        - CLAWCALL_EMAIL
    primaryEnv: CLAWCALL_API_KEY
---

# ClawCall — Phone Calls for Your Agent

ClawCall gives your agent a real phone number connected to a telephony
service (Twilio). Users can call you, you can call them back when tasks
finish, schedule recurring briefings, and place calls to third parties.

**How it works:** This skill communicates with the ClawCall service at
`https://api.clawcall.online`. All connections are outbound from your
machine — no inbound webhook URL is required.

---

## First-Time Setup

If `CLAWCALL_API_KEY` is not yet set, guide the user through registration:

1. Explain what you're about to do:
   > "To give you a phone number, I need to register with ClawCall —
   > a telephony service that connects your phone to this agent.
   > ClawCall will store your phone number to route incoming calls to you.
   > You can delete your account at any time."

2. Ask for their phone number:
   > "What phone number should I register? This is the number you'll
   > call me from. (E.164 format, e.g. +14155550100)"

3. Call the registration endpoint:

```
POST https://api.clawcall.online/api/v1/register
Content-Type: application/json

{
  "phone_number": "<user's phone in E.164 format>",
  "agent_name":   "<your agent name>"
}
```

4. Store the returned `api_key` as `CLAWCALL_API_KEY` and the returned
   `email` as `CLAWCALL_EMAIL`. Both are needed for account recovery.

5. Confirm setup with the user:
   > "Done! Your ClawCall number is **{phone_number}**.
   > Call it from {their phone} to talk to me.
   > *(Free tier: calls must come from your registered number.)*"

6. Start the listener:

   The listener is a small Node.js script that polls ClawCall for
   incoming call messages and routes them to this agent session.
   It only communicates with `api.clawcall.online` and invokes the
   local OpenClaw CLI — it does not access unrelated files or services.

   Ask the user:
   > "I need to keep a listener running so calls reach me. I can start
   > it for you right now, or give you the command to run yourself —
   > which do you prefer?"

   **If the user says "start it for me":** use your shell tool to launch
   it as a background process (requires the shell tool to be enabled):

   Mac / Linux —
   ```
   nohup node /path/to/skill/listener/clawcall-listener.js \
     > /tmp/clawcall-listener.log 2>&1 &
   ```
   Windows —
   ```
   start "" /B node C:\path\to\skill\listener\clawcall-listener.js
   ```

   Confirm it started (check PID / log file), then tell the user:
   > "Listener is running. Calls to {phone_number} will reach me in
   > real time. You can stop it any time by killing the node process."

   **If the user prefers to run it themselves** (or the shell tool is
   not available), give them this command:
   > ```
   > node listener/clawcall-listener.js
   > ```
   > "Run that in a terminal and keep it open. Every call will reach
   > me while it's running."

---

## Starting the Listener

The skill ships a listener script at `listener/clawcall-listener.js`.
It polls ClawCall for incoming call messages and routes them through
this agent session. **The listener must be running for calls to work.**

During first-time setup, offer to start it automatically (see above).
To start it manually (requires Node.js):
```
node listener/clawcall-listener.js
```

The `CLAWCALL_API_KEY` environment variable must be set before running.
The listener runs until the terminal is closed or the process is killed.

---

## Receiving Inbound Calls

**When using the listener script** (the normal setup), the listener polls
ClawCall for you and passes each caller's speech directly to this agent
via `--message`. **Do not call `/api/v1/calls/listen` yourself** — the
listener already dequeued the message. Just answer the user's question
naturally and exit. The listener captures your output and relays it back.

---

### Advanced: receiving calls without the listener script

If you are integrating ClawCall without `clawcall-listener.js`, poll
for incoming messages manually:

```
GET https://api.clawcall.online/api/v1/calls/listen?timeout=25
Authorization: Bearer {CLAWCALL_API_KEY}
```

**Response when a call is waiting:**
```json
{
  "ok": true,
  "call_sid": "CA...",
  "message": "What's the weather today?"
}
```

**Response when no call is waiting (timeout):**
```json
{ "ok": true, "timeout": true }
```

After receiving a message, submit your response:

```
POST https://api.clawcall.online/api/v1/calls/respond/{call_sid}
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{
  "response": "It's 72°F and sunny in New York.",
  "end_call": false
}
```

Set `"end_call": true` to hang up after speaking your response.
Set `"end_call": false` to keep the line open for follow-up.

> **Note on long tasks:** ClawCall automatically plays filler phrases
> ("Working on that now.", "Just a sec.", etc.) while waiting for your
> response — you do not need to send interim messages. Simply respond
> when ready; the line stays active.

### Message prefixes

| Prefix | Meaning |
|---|---|
| *(plain text)* | Normal inbound call from user |
| `[SCHEDULED] <context>` | A scheduled call fired — deliver the briefing |
| `[THIRD PARTY CALL]` | Opening turn of an autonomous third-party call |
| `[THIRD PARTY SAYS]: <text>` | Third party's response — continue the conversation |
| `[THIRD PARTY COMPLETE]` | Third-party call ended — JSON transcript follows |

---

## Calling the User Back (Pro tier)

When a background task finishes and you need to notify the user by phone:

```
POST https://api.clawcall.online/api/v1/calls/outbound/callback
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{
  "message":       "Your deployment finished. 3 services updated, 0 errors.",
  "allow_followup": true
}
```

If `allow_followup` is true, the user can ask follow-up questions after
the message. Those replies arrive via `/calls/listen` as normal.

**Requires Pro tier.**

---

## Scheduling a Recurring Call (Pro tier)

```
POST https://api.clawcall.online/api/v1/calls/schedule
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{
  "cron":         "0 8 * * 1-5",
  "label":        "Morning briefing",
  "task_context": "Summarise my calendar, top emails, and pending tasks",
  "timezone":     "America/New_York"
}
```

Common patterns: every weekday 8am `"0 8 * * 1-5"` · daily 9am `"0 9 * * *"`.

To cancel: `DELETE https://api.clawcall.online/api/v1/calls/schedule/{id}`

**Requires Pro tier.**

---

## Calling a Third Party (Pro tier)

```
POST https://api.clawcall.online/api/v1/calls/outbound/third-party
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{
  "to_number":  "+14155550100",
  "objective":  "Book a dentist appointment for next Tuesday afternoon",
  "context":    "Patient: Alex Kumar. Returning patient. Flexible on time."
}
```

ClawCall dials the number and forwards the conversation turn-by-turn via
`/calls/listen`. Set `"end_call": true` when the objective is complete.

**Requires Pro tier.**

---

## Account & Usage

```
GET https://api.clawcall.online/api/v1/account
Authorization: Bearer {CLAWCALL_API_KEY}
```

Returns tier, minutes used, minutes remaining, and assigned phone number.

---

## Changing Voice

```
POST https://api.clawcall.online/api/v1/account/voice
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{ "voice": "aria" }
```

Available voices: `aria` (default) · `joanna` · `matthew` · `amy` · `brian` · `emma` · `olivia`

---

## Upgrading to Pro or Team

Payment is accepted in **USDC on Solana mainnet**.

**Step 1 — Get payment address and amount:**
```
POST https://api.clawcall.online/api/v1/billing/checkout
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{ "tier": "pro" }
```

**Step 2 — Send USDC** to the wallet address returned above.

**Step 3 — Submit transaction signature to confirm:**
```
POST https://api.clawcall.online/api/v1/billing/verify
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{ "tx_signature": "<Solana tx hash>", "tier": "pro" }
```

Tier upgrades instantly on confirmation.

---

## Recovering a Lost API Key

Re-register with the same email to rotate the key:

```
POST https://api.clawcall.online/api/v1/register
Content-Type: application/json

{
  "email":        "{CLAWCALL_EMAIL}",
  "phone_number": "<user's phone>",
  "agent_name":   "<agent name>"
}
```

Returns a fresh `api_key`. Save it as `CLAWCALL_API_KEY`.

---

## Tier Limits

| Tier | Minutes/month | Callbacks | Scheduled | 3rd Party | Agents |
|------|--------------|-----------|-----------|-----------|--------|
| Free | 10           | No        | No        | No        | 1      |
| Pro  | 120          | Yes       | Yes       | Yes       | 1      |
| Team | 500 (pooled) | Yes       | Yes       | Yes       | 5      |

Overage: $0.05/min beyond included minutes (Pro/Team only).
