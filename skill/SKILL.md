---
name: clawcall
version: 2.0.0
description: >
  Give this agent a real phone number. Receive calls from the user,
  call user back when tasks complete, run scheduled calls, or call
  third parties on the user's behalf. No public URL or Tailscale required.
metadata:
  openclaw:
    requires:
      bins: []
      env:
        - CLAWCALL_API_KEY
    primaryEnv: CLAWCALL_API_KEY
---

# ClawCall — Phone Calls for Your Agent

ClawCall gives your agent a real phone number. Users can call you, you can
call them back, schedule briefings, and place calls to third parties.

**No webhook URL or Tailscale required.** Your agent pulls messages from
ClawCall using long-polling — all outbound connections from your machine.

---

## Setup (Runs Automatically — No Manual Steps Needed)

**On every startup**, check if `CLAWCALL_API_KEY` is set.

If `CLAWCALL_API_KEY` is NOT set:

1. Ask the user ONE question:
   > "What's your phone number? I'll use it to set up your ClawCall account
   > so you can call me directly. (e.g. +14155550100)"

2. Register immediately — no email or webhook URL needed:

```
POST https://api.clawcall.online/api/v1/register
Content-Type: application/json

{
  "phone_number": "<user's E.164 phone>",
  "agent_name":   "<your agent name>"
}
```

3. Save the returned `api_key` as `CLAWCALL_API_KEY`.
   Also save the returned `email` as `CLAWCALL_EMAIL` (for re-registration later).

4. Tell the user:
   > "You're all set! 📞 Your ClawCall number is **{phone_number}**.
   > Call it from **{their phone}** anytime to talk to me directly.
   > *(Free tier: you must call from your registered number so I know it's you.)*"

5. Immediately start the listen loop (see below).

---

## The Listen Loop (Always Running)

After setup, run this loop continuously so you never miss a call:

```
loop forever:
    GET https://api.clawcall.online/api/v1/calls/listen?timeout=25
    Authorization: Bearer {CLAWCALL_API_KEY}

    if response.timeout == true:
        continue  # no call, loop again immediately

    call_sid = response.call_sid
    message  = response.message

    reply = process(message)   # your normal agent logic

    POST https://api.clawcall.online/api/v1/calls/respond/{call_sid}
    Authorization: Bearer {CLAWCALL_API_KEY}
    Content-Type: application/json

    { "response": reply, "end_call": false }

    # keep looping — next call will come through the same endpoint
```

Set `"end_call": true` in your response to hang up after speaking.

**Important:** Respond within 25 seconds. For tasks taking longer, respond
quickly with `"On it, give me a second."` — ClawCall will keep the line
active while you work.

---

## Message Types

Messages arriving via `/calls/listen` follow these patterns:

| Message prefix | Meaning |
|---|---|
| *(plain text)* | User is speaking on an inbound call |
| `[SCHEDULED] <context>` | A scheduled call fired — deliver the briefing |
| `[THIRD PARTY CALL]` | Start of an autonomous third-party call |
| `[THIRD PARTY SAYS]: <speech>` | Third party spoke — continue the conversation |
| `[THIRD PARTY COMPLETE]` | Third-party call ended — JSON transcript follows |

---

## Calling the User Back (Pro tier)

When you finish a background task the user asked you to report on:

```
POST https://api.clawcall.online/api/v1/calls/outbound/callback
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{
  "message": "Your deployment finished. 3 services updated, 0 errors.",
  "allow_followup": true
}
```

If `allow_followup` is true, the user can ask follow-up questions after
hearing the message. The follow-up arrives via your listen loop as normal.

---

## Scheduling a Recurring Call (Pro tier)

```
POST https://api.clawcall.online/api/v1/calls/schedule
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{
  "cron": "0 8 * * 1-5",
  "label": "Morning briefing",
  "task_context": "Give me a summary of my calendar, top emails, and tasks",
  "timezone": "America/New_York"
}
```

Common cron patterns:
- Every weekday 8am: `"0 8 * * 1-5"`
- Every day 9am:     `"0 9 * * *"`
- Every Monday 7am:  `"0 7 * * 1"`

To cancel: `DELETE /api/v1/calls/schedule/{id}`

---

## Calling a Third Party (Pro tier)

```
POST https://api.clawcall.online/api/v1/calls/outbound/third-party
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{
  "to_number": "+14155550100",
  "objective": "Book a dentist appointment for next Tuesday afternoon",
  "context":   "Patient: Alex Kumar. Returning patient. Flexible on time."
}
```

The conversation arrives turn-by-turn via your listen loop:
- Opening turn: `[THIRD PARTY CALL]\nObjective: ...\nContext: ...`
- Each reply: `[THIRD PARTY SAYS]: <speech>`
- When done, set `end_call: true` in your response.
- Completion notification: `[THIRD PARTY COMPLETE]\n{json}`

---

## Checking Usage

```
GET https://api.clawcall.online/api/v1/account
Authorization: Bearer {CLAWCALL_API_KEY}
```

---

## Changing Voice

```
POST https://api.clawcall.online/api/v1/account/voice
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{ "voice": "aria" }
```

Voices: `aria` (default), `joanna`, `matthew`, `amy`, `brian`, `emma`, `olivia`.

---

## Upgrading to Pro or Team

Payment in **USDC on Solana mainnet**.

**Step 1 — Get payment details:**
```
POST https://api.clawcall.online/api/v1/billing/checkout
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{ "tier": "pro" }
```

**Step 2 — Send USDC** to the returned Solana wallet address.

**Step 3 — Confirm:**
```
POST https://api.clawcall.online/api/v1/billing/verify
Authorization: Bearer {CLAWCALL_API_KEY}
Content-Type: application/json

{ "tx_signature": "<Solana tx hash>", "tier": "pro" }
```

---

## Re-registration (If API Key Is Lost)

```
POST https://api.clawcall.online/api/v1/register
Content-Type: application/json

{
  "email":        "{CLAWCALL_EMAIL}",
  "phone_number": "<user's phone>",
  "agent_name":   "<agent name>"
}
```

Returns a new `api_key`. Save it as `CLAWCALL_API_KEY`.

---

## Tier Limits

| Tier | Minutes/month | Callbacks | Scheduled | 3rd Party | Agents |
|------|--------------|-----------|-----------|-----------|--------|
| Free | 10           | No        | No        | No        | 1      |
| Pro  | 120          | Yes       | Yes       | Yes       | 1      |
| Team | 500 (pooled) | Yes       | Yes       | Yes       | 5      |

Overage: $0.05/min beyond included minutes (Pro/Team only).
