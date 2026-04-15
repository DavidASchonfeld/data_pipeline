# The Right Link to Share — and Why

## The link to put on your resume or portfolio

```
https://im6g5ue81k.execute-api.us-east-1.amazonaws.com/dashboard/
```

The weather page works the same way — just change the path:

```
https://im6g5ue81k.execute-api.us-east-1.amazonaws.com/weather/
```

These links always work. Click it at any time of day and you will either see the dashboard
immediately (if the server is already running) or a loading screen that brings the server
online automatically (if it was sleeping). Either way, you never get an error.

These replace the old direct-IP URLs that were previously shared publicly:

| Old URL (no longer reliable) | New URL |
|---|---|
| `http://52.70.211.1:32147/dashboard/` | `https://im6g5ue81k.execute-api.us-east-1.amazonaws.com/dashboard/` |
| `http://52.70.211.1:32147/weather/` | `https://im6g5ue81k.execute-api.us-east-1.amazonaws.com/weather/` |

The old URLs still work while the server is running — they just fail silently (connection
error with no explanation) when the server is sleeping. The new URLs always work.

---

## Why this changed — the "always-on" years vs. today

### Before: server ran 24/7

When a server runs around the clock, its public IP address is always reachable. AWS gives
you a static IP (called an Elastic IP) so that address never changes. The flow looked like this:

```
Someone clicks your link
        ↓
52.70.211.1:32147  ← server is always on, always listening, always answers
        ↓
Dashboard loads instantly
```

The IP address was a perfectly good link to share because something was always sitting
behind it, ready to respond.

### Today: server sleeps when idle

The server now shuts itself down after 45 minutes of inactivity. This saves roughly $60 a
month compared to running it continuously on standard hardware. But it creates a problem
with the IP address.

When the server shuts down, AWS detaches the Elastic IP from it. The address still exists,
but it is like a phone number for an office that is currently locked and empty. Call it
and nobody picks up. Your browser shows "connection dropped" instead of a loading screen.

```
Someone clicks your link at 9 a.m. (server went to sleep at midnight)
        ↓
52.70.211.1:32147  ← address exists, but nothing is behind it
        ↓
"Safari can't open the page" ← dead end
```

---

## What API Gateway is — and why it solves this

Think of API Gateway as a **permanent receptionist** who never sleeps, never takes
holidays, and costs almost nothing to keep at the desk.

The receptionist's desk address — the URL above — is published in AWS's own
infrastructure, not on your server. It exists independently of whether your server is on,
off, sleeping, or in the middle of being replaced. It never moves.

When someone visits that link:

- **Server is sleeping:** the receptionist wakes it up and shows the visitor a "hold on,
  starting up" loading screen while they wait.
- **Server is already running:** the receptionist checks that everything looks healthy,
  then sends the visitor straight through to the live dashboard.
- **Server is mid-boot:** the receptionist shows the loading screen until it is ready.

The visitor never hits a dead end.

```
Someone clicks your link at any time
        ↓
API Gateway  ← always alive, lives in Amazon's infrastructure, not your server
        ↓
   Is server on?
   ┌────────────────────────────────────────────────────┐
   │ No  → wake it up → show loading screen (3–5 min)  │
   │ Yes → check it is healthy → send visitor through  │
   └────────────────────────────────────────────────────┘
        ↓
Dashboard loads
```

---

## Why it is essentially free

AWS charges for API Gateway based on the number of requests it receives — roughly
**$1 per million requests**. A portfolio link visited by a handful of recruiters might
receive a few dozen requests a month. At that volume the cost rounds to **$0.00**.

For comparison: the Elastic IP itself costs about $3.65 a month whenever the server is
sleeping (AWS charges for reserved IPs that are not attached to a running instance).
API Gateway has no standing charge — it only costs anything when someone actually clicks
the link, and even then only fractions of a cent.

---

## The Elastic IP — what it is still used for

The Elastic IP (`52.70.211.1`) is not wasted. It still serves two important purposes:

1. **SSH access.** Connecting to the server from a terminal uses the IP address directly.
   Having a static IP means the SSH shortcut (`ssh ec2-stock`) always works without
   updating any config files after a server replacement.

2. **The final destination.** When the API Gateway determines the server is healthy and
   ready, it sends the visitor's browser to the Elastic IP address to load the actual
   dashboard. The IP just can no longer be the *first* link a visitor hits, because it
   only works while the server is running.

---

## Summary

| | Elastic IP (`52.70.211.1:32147`) | API Gateway URL |
|---|---|---|
| Always reachable | Only when server is running | Yes — 24/7 |
| Shows loading screen when server sleeps | No — connection error | Yes |
| Changes when server is replaced | No (that is its purpose) | No |
| Changes when deploy script runs | No | No |
| Good link to share publicly | Only if server never sleeps | Yes |
| Monthly cost | ~$3.65/month when server is sleeping | ~$0.00 |

**Use the API Gateway URL for your resume and portfolio. Use the Elastic IP for SSH.**
