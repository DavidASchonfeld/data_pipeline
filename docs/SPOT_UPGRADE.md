# Spot Instance Upgrade — What Changed and Why

## The Short Version

The server that runs this pipeline was upgraded to use cheaper hardware,
and a safety net was added so it automatically recovers if AWS shuts it down.
The deploy script was also fixed to handle a fresh server correctly.

---

## Background: What Is a "Spot Instance"?

AWS lets you rent spare computing capacity at a steep discount (often 60–80%
cheaper). The catch: AWS can reclaim it with 2 minutes notice if someone else
needs it. This is called a **spot instance**.

Previously the pipeline ran on a regular on-demand server (`t3.large`), which
costs full price and stays up indefinitely. Switching to spot saves money, but
requires handling the possibility of unexpected shutdowns.

---

## Change 1: Switched to ARM Chips

Normal laptops and servers use **x86** chips (Intel/AMD). ARM chips (used in
Apple Silicon Macs, and AWS's Graviton line) do the same work but use less
power and cost less.

The server was switched from `t3.large` (x86) to `t4g.large` (ARM). The
software runs identically — it just costs less per hour.

---

## Change 2: Auto-Restart via ASG + Lambda

**ASG (Auto Scaling Group)** is an AWS feature that watches over a group of
servers. Even when set to "always keep exactly 1 running," it will
automatically launch a replacement if the current one is shut down — whether
that's an AWS spot interruption, a crash, or anything else.

**Lambda** is a small piece of code that AWS runs automatically in response to
events — no server needed. Here, a Lambda function runs every time the ASG
launches a new instance. Its one job: re-attach the pipeline's fixed IP address
to the new machine, so SSH access and DNS names continue to work without any
manual steps.

The chain looks like this:

```
ASG spots new instance → fires an event → SNS (notification service)
→ Lambda runs → re-attaches the IP address → instance is ready
```

---

## Change 3: Deploy Script Fixed for Fresh Servers

The deploy script (`./scripts/deploy.sh`) previously assumed the server was
already set up. On a brand-new spot instance, none of the required software
(Docker, K3s, Helm, etc.) is installed yet, so the old script would crash
immediately.

Fixes made:

- **Auto-bootstrap**: if the script detects a fresh server and `--provision`
  was passed, it installs everything automatically before continuing.
- **Docker fix**: Ubuntu's built-in Docker package was missing a required
  plugin (`buildx`). The script now installs the official Docker package
  instead, which includes it.
- **Patience on boot**: a new server takes 2–3 minutes to finish starting up.
  The script now waits for it rather than failing immediately.
- **Database startup**: the Airflow database takes several minutes to
  initialize on a fresh server. The script now waits for it to be ready
  before moving on, instead of crashing with a timeout.
- **Faster deploys**: the dashboard (Flask) now starts building at the same
  time as other components instead of waiting for them to finish first —
  shaving roughly 10 minutes off a full deploy.
- **Cleaner failures**: if something goes wrong mid-deploy, background tasks
  are now stopped cleanly so the terminal doesn't appear frozen.

---

## Summary Table

| What | Before | After |
|---|---|---|
| Server type | x86 on-demand (`t3.large`) | ARM spot (`t4g.large`) |
| If server shuts down | Manual recovery | ASG + Lambda restart automatically |
| IP address after restart | Lost — must update SSH config | Same fixed IP re-attached by Lambda |
| Fresh server support | Script crashes | Script installs everything via `--provision` |
| Deploy time | Sequential steps | Parallel steps (~10 min faster) |
| Pip installs on startup | Every pod restart (~7 min) | Baked into Docker image (0 min) |
