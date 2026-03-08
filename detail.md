# ClawCall — Full Project Context

> Twilio-powered voice calling skill for OpenClaw agents. Paste this into Claude Code to start building.

---

## What is ClawCall?

ClawCall is an OpenClaw skill + hosted backend service that gives every OpenClaw agent a phone number. Users install the skill, the agent handles registration automatically, and from that point on:

- Users can **call their agent** and talk to it naturally
- The agent can **call the user back** when background tasks complete
- Users can **schedule calls** with their agent (daily briefings, reminders, etc.)
- The agent can **call third parties** on the user's behalf (book appointments, make inquiries, etc.)

The key design principle: **zero credential complexity for the user.** No Twilio accounts, no API keys, no configuration. The agent handles everything by talking to the ClawCall backend API.

---

## How OpenClaw Skills Work (Important Context)

OpenClaw is an open-source autonomous AI agent (formerly Clawdbot/Moltbot) built by Peter Steinberger. It runs locally on the user's machine and connects to messaging platforms (WhatsApp, Telegram, Discord, etc.) as its UI.

Skills are the extension mechanism:

- A skill is a **folder** containing a `SKILL.md` file with YAML frontmatter + markdown instructions
- No SDK, compilation, or special runtime required
- The `SKILL.md` teaches the agent HOW to use a tool/API in a repeatable way
- Skills are distributed via **ClawHub** (OpenClaw's public skill registry)
- Users install by saying "install clawcall" and the agent pulls it from ClawHub automatically

Skill folder structure:

```
twilio-voice/
├── SKILL.md          ← agent instructions + YAML metadata (required)
├── server.js         ← local webhook middleware server
├── package.json
└── references/
    └── setup.md      ← internal docs the agent can reference
```

SKILL.md format:

```yaml
---
name: clawcall
description: >
  Make and receive phone calls. Users can call this agent and speak
  naturally to execute tasks. Agent can call user when tasks complete,
  call on a schedule, or call third parties on user's behalf.
metadata:
  openclaw:
    requires:
      bins: ["node"]
      env:
        - CLAWCALL_API_KEY   ← only credential user needs (auto-provisioned)
---
# Markdown instructions for the agent follow here...
```

OpenClaw exposes each agent via a publicly reachable URL (via Tailscale Funnel or SSH tunnel). This is how the ClawCall backend can forward incoming calls to the right agent.

---

## Product: Tiers & Pricing

### Free Tier

- 1 **shared** phone number (shows "ClawCall" as caller ID, not dedicated)
- 10 minutes/month total (inbound + outbound combined)
- Inbound calls only (user calls agent)
- No 3rd party calling
- TTS voice has "Powered by ClawCall" watermark
- 1 agent only

### Pro — $9/month

- 1 **dedicated** phone number
- 120 minutes/month
- Inbound + outbound (task completion callbacks + scheduled calls)
- 3rd party calling enabled (agent calls someone else on user's behalf)
- Custom voice selection
- Full call transcripts + history dashboard
- 1 agent

### Team — $29/month

- Up to 5 dedicated numbers (1 per agent)
- 500 minutes/month (pooled across agents)
- Everything in Pro
- Multi-agent support
- Webhook call logs (push call events to user's own systems)
- Priority routing (lower latency queue)

### Pay-as-you-go Overage (Pro + Team)

- $0.05/minute beyond included minutes
- No hard cutoffs — just charges overage

### Cost Structure (Twilio)

- ~$1/month per dedicated Twilio number
- ~$0.013/minute per call (Twilio rate)
- Your effective margin on Pro: ~$0.037/minute
- At 100 Pro users: ~$900 MRR, ~$200 Twilio costs

---

## Call Types — Full Specification

### 1. Inbound Call (User → Agent)

The core feature. User dials their number, has a real-time conversation with their agent.

**Flow:**

```
User dials their ClawCall number
→ Twilio receives call, hits ClawCall webhook
→ ClawCall backend looks up agent from phone number
→ Opens WebSocket connection to user's OpenClaw agent (via agent_webhook_url)
→ Twilio streams audio → ClawCall STT → text sent to agent
→ Agent processes, executes tasks, returns text response
→ ClawCall TTS → audio streamed back to Twilio → user hears response
→ Loop continues until user hangs up
```

**Latency handling:** While agent is executing long-running tasks (web browsing, code execution, API calls), ClawCall must speak interim phrases like "On it, give me a second..." or "Working on that now..." to prevent silence/timeout. Agent sends a `thinking` signal when processing begins.

**Session continuity:** Each call is tied to a `CallSid` (Twilio's unique call identifier). Map `CallSid → conversation_thread_id` so the full conversation stays coherent across multi-turn exchanges within one call.

---

### 2. Outbound — Task Completion Callback

Agent calls user when a background task finishes.

**Flow:**

```
User tells agent: "Deploy this PR and call me when it's done"
→ Agent starts background task
→ Agent registers callback: POST /api/v1/calls/outbound/callback
   { user_id, message: "Your PR was deployed successfully. Here's the summary..." }
→ ClawCall backend dials user's registered phone number
→ Agent speaks the result when user picks up
→ User can ask follow-up questions (becomes interactive inbound session)
```

**API call from agent to ClawCall:**

```json
POST /api/v1/calls/outbound/callback
Authorization: Bearer {CLAWCALL_API_KEY}
{
  "message": "Your deployment finished. 3 services updated, 0 errors.",
  "allow_followup": true
}
```

---

### 3. Outbound — Scheduled Call

User pre-schedules recurring or one-time calls with their agent.

**Flow:**

```
User: "Call me every weekday at 8am with a morning briefing"
→ Agent registers schedule: POST /api/v1/calls/schedule
   { cron: "0 8 * * 1-5", task_context: "morning briefing" }
→ ClawCall cron scheduler fires at scheduled time
→ Dials user's number
→ Agent delivers briefing (pulls from calendar, email, tasks, etc.)
→ User can interact/redirect the briefing
```

**Cron API:**

```json
POST /api/v1/calls/schedule
Authorization: Bearer {CLAWCALL_API_KEY}
{
  "cron": "0 8 * * 1-5",
  "label": "Morning briefing",
  "task_context": "Give me a summary of my calendar, top emails, and any pending tasks",
  "timezone": "America/New_York"
}
```

---

### 4. Outbound — 3rd Party Call (Pro+ only)

Agent calls someone ELSE on the user's behalf. Most powerful feature.

**Flow:**

```
User: "Call my dentist at +1-415-555-0100 and book an appointment for next Tuesday afternoon"
→ Agent triggers: POST /api/v1/calls/outbound/third-party
   { to_number: "+14155550100", objective: "Book appointment for next Tuesday afternoon",
     context: "Patient name: Aayush, DOB: ..., Insurance: ..." }
→ ClawCall dials the third party number
→ Agent handles the conversation AUTONOMOUSLY (no user on the call)
→ Call recording + full transcript generated
→ ClawCall POSTs result back to agent webhook when done
→ Agent notifies user: "Done! Appointment booked for Tuesday 3pm."
```

**3rd Party API:**

```json
POST /api/v1/calls/outbound/third-party
Authorization: Bearer {CLAWCALL_API_KEY}
{
  "to_number": "+14155550100",
  "objective": "Book a dentist appointment for next Tuesday afternoon",
  "context": "Patient: Aayush Kumar. Returning patient.",
  "callback_on_complete": true
}
```

**Note:** Pro tier required. This uses Twilio outbound calls billed at ~$0.013/min from ClawCall's account.

---

## Backend Architecture

### Tech Stack (Recommended)

- **Runtime:** Node.js (matches OpenClaw's ecosystem)
- **Framework:** Express.js
- **Database:** PostgreSQL (users, numbers, call logs, schedules)
- **Queue/Scheduler:** BullMQ + Redis (for scheduled calls and task queues)
- **Billing:** Stripe (subscriptions + usage metering)
- **Telephony:** Twilio Voice + Media Streams API
- **STT:** Twilio's built-in speech recognition OR Deepgram (lower latency)
- **TTS:** Twilio `<Say>` with Polly voices OR ElevenLabs (Pro custom voice)
- **WebSocket:** For real-time audio streaming between Twilio and OpenClaw agent

### Database Schema

```sql
-- Users
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  tier TEXT DEFAULT 'free' CHECK (tier IN ('free', 'pro', 'team')),
  stripe_customer_id TEXT,
  stripe_subscription_id TEXT,
  minutes_used_this_month INTEGER DEFAULT 0,
  minutes_limit INTEGER DEFAULT 10,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Agents (users can have multiple on Team tier)
CREATE TABLE agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  name TEXT,
  webhook_url TEXT NOT NULL,  -- OpenClaw agent's public URL
  clawcall_api_key TEXT UNIQUE NOT NULL,  -- key stored in agent's SKILL config
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Phone Numbers
CREATE TABLE phone_numbers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  twilio_sid TEXT UNIQUE NOT NULL,
  number TEXT UNIQUE NOT NULL,  -- e.g. +14155550192
  agent_id UUID REFERENCES agents(id),
  is_dedicated BOOLEAN DEFAULT false,
  is_shared_pool BOOLEAN DEFAULT false,
  assigned_at TIMESTAMPTZ DEFAULT now()
);

-- Call Logs
CREATE TABLE call_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID REFERENCES agents(id),
  twilio_call_sid TEXT UNIQUE,
  direction TEXT CHECK (direction IN ('inbound', 'outbound')),
  call_type TEXT CHECK (call_type IN ('user_initiated', 'task_callback', 'scheduled', 'third_party')),
  from_number TEXT,
  to_number TEXT,
  duration_seconds INTEGER,
  transcript TEXT,
  recording_url TEXT,
  status TEXT,  -- completed, failed, in-progress
  started_at TIMESTAMPTZ,
  ended_at TIMESTAMPTZ
);

-- Scheduled Calls
CREATE TABLE scheduled_calls (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID REFERENCES agents(id),
  label TEXT,
  cron_expression TEXT NOT NULL,
  task_context TEXT,
  timezone TEXT DEFAULT 'UTC',
  is_active BOOLEAN DEFAULT true,
  last_run_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### API Routes

```
POST   /api/v1/register                     ← agent self-registers, gets API key + number
GET    /api/v1/account                      ← get current tier, usage, number

POST   /api/v1/calls/outbound/callback      ← agent triggers callback to user
POST   /api/v1/calls/outbound/third-party   ← agent triggers 3rd party call
POST   /api/v1/calls/schedule               ← create/update scheduled call
DELETE /api/v1/calls/schedule/:id           ← remove scheduled call
GET    /api/v1/calls/history                ← get call logs + transcripts

POST   /webhooks/twilio/inbound             ← Twilio hits this on incoming call
POST   /webhooks/twilio/status              ← Twilio call status updates
WS     /ws/call/:call_sid                   ← WebSocket for real-time audio bridge
```

### System Architecture Diagram

```
┌─────────────┐     ┌──────────────────────────┐     ┌──────────────────────┐
│   TWILIO    │────▶│   CLAWCALL BACKEND        │────▶│  USER'S OPENCLAW     │
│             │     │                           │     │  AGENT               │
│  Inbound    │     │  - Webhook receiver       │     │  (runs on their      │
│  Outbound   │     │  - Number provisioning    │     │   machine, public    │
│  Streaming  │     │  - Agent routing          │     │   URL via Tailscale) │
│  Recording  │◀────│  - STT/TTS pipeline       │◀────│                      │
└─────────────┘     │  - Cron scheduler         │     └──────────────────────┘
                    │  - Minute tracking        │
                    │  - Billing (Stripe)       │
                    └──────────────┬────────────┘
                                   │
                         ┌─────────┴──────────┐
                         │    PostgreSQL       │
                         │    Redis (BullMQ)   │
                         └────────────────────┘
```

---

## User Registration Flow (Zero-friction Install)

```
1. User tells their OpenClaw agent: "Install clawcall"

2. Agent pulls skill from ClawHub

3. SKILL.md instructs agent to:
   - Ask user: "What email should I use to set up your ClawCall account?"
   - User replies with email

4. Agent calls: POST /api/v1/register
   { email: "user@example.com", agent_webhook_url: "https://agent.tail1234.ts.net" }

5. Backend:
   - Creates user account (free tier)
   - Assigns number from shared pool (free) or provisions dedicated Twilio number (paid)
   - Generates CLAWCALL_API_KEY
   - Returns: { api_key, phone_number, tier }

6. Agent stores CLAWCALL_API_KEY in skill config
   (user never sees or manages this)

7. Agent tells user:
   "You're all set! Your agent number is +1 (415) 555-0192.
    Call me anytime, or I'll call you when I finish background tasks."
```

---

## SKILL.md — Full Draft

```markdown
---
name: clawcall
description: >
  Give this agent a phone number. Receive calls from the user, call user back
  when tasks complete, make scheduled calls, or call third parties on the user's behalf.
  Handles all Twilio/phone infrastructure automatically via the ClawCall service.
metadata:
  openclaw:
    requires:
      bins: ["node", "npm"]
      env:
        - CLAWCALL_API_KEY
    primaryEnv: CLAWCALL_API_KEY
---

# ClawCall — Phone Calls for Your Agent

ClawCall gives you a real phone number. You can receive calls, call the user back
when tasks finish, run scheduled call briefings, and place calls to third parties.

## Setup (First Time)

If CLAWCALL_API_KEY is not set:

1. Ask the user for their email address
2. Call POST https://api.clawcall.com/api/v1/register with their email and your webhook URL
3. Store the returned api_key as CLAWCALL_API_KEY
4. Tell the user their phone number and that setup is complete

## Handling an Incoming Call

When the user calls, ClawCall streams their speech as text to your webhook.
Respond naturally. Execute tasks as requested. For tasks taking >3 seconds,
say something like "On it, one second..." before executing so the line stays active.

## Calling the User Back (Task Completion)

When you finish a background task the user asked you to report on:
POST https://api.clawcall.com/api/v1/calls/outbound/callback
Headers: Authorization: Bearer {CLAWCALL_API_KEY}
Body: { "message": "<result summary>", "allow_followup": true }

## Scheduling a Recurring Call

When the user asks you to call them on a schedule:
POST https://api.clawcall.com/api/v1/calls/schedule
Headers: Authorization: Bearer {CLAWCALL_API_KEY}
Body: { "cron": "<cron expression>", "label": "<name>", "task_context": "<what to do>", "timezone": "<tz>" }

## Calling a Third Party (Pro tier)

When the user asks you to call someone else:
POST https://api.clawcall.com/api/v1/calls/outbound/third-party
Headers: Authorization: Bearer {CLAWCALL_API_KEY}
Body: { "to_number": "<number>", "objective": "<what to accomplish>", "context": "<relevant info>" }
You will receive a webhook with the transcript when the call ends.

## Checking Usage

GET https://api.clawcall.com/api/v1/account
Headers: Authorization: Bearer {CLAWCALL_API_KEY}
Returns current tier, minutes used, minutes remaining, phone number.
```

---

## Build Phases

### Phase 1 — MVP (Build First)

- [ ] Backend: Express server with `/register`, `/account` endpoints
- [ ] Twilio: inbound call webhook → STT → forward to agent → TTS → respond
- [ ] Database: users, agents, phone_numbers, call_logs tables
- [ ] Number provisioning: shared pool for free, dedicated for paid
- [ ] SKILL.md: registration flow + inbound call handling instructions
- [ ] Publish to ClawHub

### Phase 2

- [ ] Outbound task completion callbacks
- [ ] Scheduled calls (BullMQ cron jobs)
- [ ] Call transcripts + history API endpoint
- [ ] Stripe billing integration (Pro tier)

### Phase 3

- [ ] 3rd party autonomous calling
- [ ] Team tier + multi-agent support
- [ ] Custom voice selection (ElevenLabs integration)
- [ ] Webhook call log push for Team tier
- [ ] Dashboard UI (optional web frontend)

---

## Key Technical Decisions & Notes

**Twilio Media Streams vs. Gather:**

- Use Twilio Media Streams (WebSocket) for real-time streaming audio — lower latency, more natural feel
- Avoid `<Gather>` + turn-by-turn for the main call experience (too clunky)

**STT options:**

- Twilio built-in: easiest, ~300ms latency
- Deepgram: faster (~100ms), better accuracy, slightly more setup
- Recommend starting with Twilio built-in for MVP

**Agent reachability:**

- OpenClaw agents expose a public URL via Tailscale Funnel automatically
- Store this `agent_webhook_url` at registration time
- For the call bridge: ClawCall backend forwards STT output as a POST to this URL, waits for agent response, sends back as TTS

**Interim responses during long tasks:**

- Agent must signal "thinking" state immediately
- ClawCall backend should start TTS filler phrases ("Working on that...") if no agent response within 2 seconds
- Prevents Twilio from closing the call due to silence timeout (default 5 seconds)

**Minute tracking:**

- Increment `minutes_used_this_month` on call end using Twilio's `CallDuration` from status webhook
- Reset on 1st of each month (cron job)
- On free tier hitting 10 min limit: gracefully end call, tell user they've hit their limit

**Security:**

- CLAWCALL_API_KEY is a UUID v4 — generated on registration, stored hashed in DB
- All agent→backend calls require this key in Authorization header
- Twilio webhook signature validation (X-Twilio-Signature header) on all inbound webhooks
- Never expose Twilio credentials to agents/users

---

## Environment Variables Needed (Backend)

```env
# Server
PORT=3000
NODE_ENV=production

# Database
DATABASE_URL=postgresql://...

# Redis
REDIS_URL=redis://...

# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_WEBHOOK_BASE_URL=https://api.clawcall.com

# Stripe
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Pricing (Stripe Price IDs)
STRIPE_PRO_PRICE_ID=price_...
STRIPE_TEAM_PRICE_ID=price_...
```

---

## Suggested Folder Structure (Backend Repo)

```
clawcall-backend/
├── src/
│   ├── index.js              ← Express app entry
│   ├── routes/
│   │   ├── register.js
│   │   ├── account.js
│   │   ├── calls.js          ← outbound callback, 3rd party, schedule
│   │   └── webhooks.js       ← Twilio inbound + status webhooks
│   ├── services/
│   │   ├── twilio.js         ← Twilio client + provisioning helpers
│   │   ├── bridge.js         ← WebSocket audio bridge logic
│   │   ├── scheduler.js      ← BullMQ cron job setup
│   │   ├── billing.js        ← Stripe helpers
│   │   └── minutes.js        ← Usage tracking + limit enforcement
│   ├── db/
│   │   ├── client.js         ← PostgreSQL client
│   │   └── migrations/       ← SQL migration files
│   └── middleware/
│       ├── auth.js           ← CLAWCALL_API_KEY validation
│       └── twilioSig.js      ← Twilio webhook signature validation
├── skill/
│   ├── SKILL.md
│   ├── references/
│   │   └── setup.md
│   └── package.json
├── .env.example
├── package.json
└── README.md
```

---

_This document contains the complete design for ClawCall. Start with Phase 1 MVP — get inbound calls working end-to-end first, then layer on outbound and scheduling._
