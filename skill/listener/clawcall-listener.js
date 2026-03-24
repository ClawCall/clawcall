#!/usr/bin/env node
/**
 * ClawCall Listener
 * Polls ClawCall for inbound call messages and routes them through
 * the local OpenClaw agent, then returns the reply to the caller.
 *
 * Usage:
 *   node clawcall-listener.js
 *
 * Requires:
 *   CLAWCALL_API_KEY environment variable set
 *   OpenClaw CLI available in PATH (openclaw command)
 */

"use strict";

const https      = require("https");
const { execSync } = require("child_process");

const API_KEY = process.env.CLAWCALL_API_KEY;

if (!API_KEY) {
  console.error("[ClawCall] ERROR: CLAWCALL_API_KEY is not set.");
  console.error("[ClawCall] Run:  setx CLAWCALL_API_KEY your-api-key  (Windows)");
  console.error("[ClawCall] or:   export CLAWCALL_API_KEY=your-api-key  (Mac/Linux)");
  process.exit(1);
}

// ── HTTP helper ──────────────────────────────────────────────────────────────

function request(method, path, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req  = https.request(
      {
        hostname: "api.clawcall.online",
        path,
        method,
        headers: {
          Authorization:   `Bearer ${API_KEY}`,
          "Content-Type":  "application/json",
          ...(data ? { "Content-Length": Buffer.byteLength(data) } : {}),
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (chunk) => (raw += chunk));
        res.on("end", () => {
          try { resolve(JSON.parse(raw)); }
          catch { resolve({ ok: false, raw }); }
        });
      }
    );
    req.on("error", reject);
    req.setTimeout(35_000, () => req.destroy(new Error("HTTP timeout")));
    if (data) req.write(data);
    req.end();
  });
}

// ── Agent turn ───────────────────────────────────────────────────────────────

function runAgentTurn(message) {
  let raw = "";
  try {
    raw = execSync(
      `openclaw agent --message ${JSON.stringify(message)} --json`,
      { timeout: 28_000, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] }
    ).trim();
  } catch (err) {
    // openclaw may exit non-zero when warnings are present; stdout may still
    // contain the actual reply — recover it from the error object before
    // falling back to a generic message.
    raw = (err.stdout || "").trim();
    if (!raw) {
      console.error("[ClawCall] Agent error:", err.message);
      return "I had a little trouble with that — could you repeat it?";
    }
  }

  // openclaw --json may output one or more JSON lines; take the last one
  const lines = raw.split("\n").filter(Boolean).reverse();
  for (const line of lines) {
    try {
      const parsed = JSON.parse(line);
      const reply  = parsed.reply ?? parsed.text ?? parsed.message ?? parsed.content;
      if (reply) return String(reply).trim();
    } catch { /* not JSON, keep trying */ }
  }

  // Fallback: return raw stdout if no JSON found
  return raw || "I'm here — could you say that again?";
}

// ── Main loop ────────────────────────────────────────────────────────────────

async function main() {
  console.log("[ClawCall] Listener started. Waiting for calls...");

  while (true) {
    try {
      const res = await request("GET", "/api/v1/calls/listen?timeout=25");

      if (res.timeout) continue;   // no call arrived, loop immediately

      if (!res.ok || !res.call_sid) {
        console.error("[ClawCall] Unexpected response:", res);
        await sleep(3_000);
        continue;
      }

      const { call_sid, message } = res;
      console.log(`[ClawCall] ↓ call_sid=${call_sid}  message="${message}"`);

      const reply = runAgentTurn(message);
      console.log(`[ClawCall] ↑ reply="${reply}"`);

      await request("POST", `/api/v1/calls/respond/${call_sid}`, {
        response: reply,
        end_call: false,
      });

    } catch (err) {
      console.error("[ClawCall] Loop error:", err.message);
      await sleep(3_000);  // back-off before retrying
    }
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

main();
