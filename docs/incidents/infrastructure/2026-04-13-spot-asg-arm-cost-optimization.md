# Spot + ASG + ARM Cost Optimization — April 13, 2026

**Date:** 2026-04-13
**Severity:** Low (cost optimization)
**Affected component:** EC2 instance, Terraform config, deploy scripts

---

## What was the problem

The pipeline's server cost ~$70-75/month, but it was only doing real work for about 29 minutes per day. The rest of the time it sat idle. Four things were making this more expensive than it needed to be:

**1. Paying full price for a server that's almost always idle.**
AWS offers "spot" pricing — you use spare capacity at a 75% discount in exchange for the possibility that AWS might reclaim the server with a 2-minute warning. Since the pipeline already handles restarts gracefully (Kafka, Airflow, and the dashboard all pick up where they left off), spot pricing is a natural fit.

**2. Using an older, more expensive chip architecture.**
The server used an Intel-based instance (t3.large). AWS makes their own ARM-based chips called Graviton, which cost ~20% less for the same specs. Every piece of software in the pipeline already supports ARM — no code changes needed.

**3. Way too much disk space.**
The server had a 100 GiB disk, but was only using ~12-14 GiB. That's like renting a 5-bedroom house for one person. A 30 GiB disk provides plenty of room at a third of the cost.

**4. The dashboard took too long to come online during deploys.**
During a deploy, the dashboard had to wait ~20-25 minutes because it was stuck behind Kafka's slow startup. But the dashboard doesn't use Kafka at all — it reads directly from Snowflake. This meant anyone visiting the site during a deploy (like a recruiter checking the portfolio) would see nothing for 20+ minutes.

---

## What was changed

### 1. Switched from Intel to ARM (t3.large → t4g.large)

Updated the server image, bootstrap script, and build tools to use ARM versions:
- `terraform/main.tf`: Server image filter changed from `amd64` to `arm64`
- `terraform/variables.tf`: Default instance type changed from `t3.large` to `t4g.large`
- `scripts/bootstrap_ec2.sh`: AWS CLI download changed from the Intel version to the ARM version
- `scripts/deploy/flask.sh`: Docker build tool changed from the Intel version to the ARM version

### 2. Switched from a fixed server to a self-healing spot server

Instead of one permanent server (`aws_instance`), the infrastructure now uses an Auto Scaling Group (ASG) — a managed group that always keeps exactly one spot server running. If AWS reclaims the spot server, the ASG automatically launches a replacement.

Key settings:
- Always keeps exactly 1 server running (min=1, max=1)
- Uses 100% spot pricing (no full-price fallback by default)
- Tries t4g.large first, falls back to t4g.xlarge if no spot capacity is available
- Spreads across multiple availability zones to reduce the chance of interruption

### 3. Automatic IP address re-assignment via Lambda

The pipeline uses a fixed IP address (Elastic IP) so that the SSH config and dashboard URL never change. Previously, Terraform assigned this IP once when the server was created. But with spot instances, the server can be replaced at any time, and each replacement gets a new internal ID.

To solve this, a small Lambda function automatically re-assigns the Elastic IP every time the ASG launches a new server. The flow:

```
ASG launches a new spot server
  → A lifecycle hook pauses the launch briefly
  → It sends a notification to an SNS topic
  → SNS triggers the Lambda function
  → Lambda assigns the Elastic IP to the new server
  → Lambda tells the ASG "all done, continue the launch"
```

If the Lambda fails for any reason, the server still launches — it just won't have the IP attached until someone fixes it manually.

**Why not use a load balancer?** A load balancer (ALB) costs ~$16/month minimum. That's almost as much as the spot server itself. For a single-server setup, the Lambda + Elastic IP approach gives the same result for free.

New files:
- `terraform/lambda/eip_reassociate.py` — the Lambda function code
- Various IAM roles and policies in `terraform/main.tf` to give Lambda and the ASG the permissions they need

### 4. Dashboard no longer waits for Kafka during deploys

In `scripts/deploy.sh`, the dashboard build now starts immediately in the background instead of waiting for Kafka and MLflow to finish first. Since the dashboard talks to Snowflake (not Kafka), there was no reason for it to wait. The Kafka/MLflow wait was moved to later in the deploy, right before the Airflow restart (which actually needs them).

Result: dashboard comes online in ~8-10 minutes instead of ~20-25 minutes.

### 5. Shrunk the disk from 100 GiB to 30 GiB

Changed the default in `terraform/variables.tf`. Actual disk usage is ~12-14 GiB, so 30 GiB gives over 2x headroom.

### 6. Cleaned up the Terraform wrapper script

`scripts/deploy/terraform.sh`:
- Removed leftover code that referenced the old single-server setup
- Updated a safety check that auto-snapshots the disk before risky changes

---

## Cost comparison

| Category | Before | After |
|----------|--------|-------|
| Instance type | t3.large (Intel, full price) | t4g.large (ARM, spot) |
| Server cost / month | ~$60 | ~$14-15 |
| Disk (EBS) | 100 GiB (~$8/mo) | 30 GiB (~$2.40/mo) |
| Elastic IP | $0 (attached to running server) | $0 (attached via Lambda) |
| ASG / Lambda / SNS | — | $0 (free tier) |
| Container registry (ECR) | ~$0.10 | ~$0.10 |
| **Total monthly** | **~$70-75** | **~$19-20** |
| **Savings** | — | **~73% reduction** |

| Metric | Before | After |
|--------|--------|-------|
| Deploy time (dashboard visible) | ~20-25 min | ~8-10 min |
| Recovery from spot interruption (with pre-baked image) | N/A | ~3-5 min automatic |
| Recovery from spot interruption (without pre-baked image) | N/A | ~30-45 min |

---

## Pre-baked AMI (speeds up spot recovery from ~30 min to ~3-5 min)

After the first successful deploy, you can take a snapshot of the running server and save it as a custom AMI (machine image). When the ASG needs to launch a replacement, it boots from this snapshot instead of starting from a blank Ubuntu install — skipping the entire 30-45 min bootstrap and deploy process.

```bash
# Find the current instance ID
INSTANCE_ID=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names pipeline-asg \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)

# Create the image (no reboot — the server stays online)
aws ec2 create-image \
  --instance-id "$INSTANCE_ID" \
  --name "pipeline-ami-$(date +%Y%m%d)" \
  --description "Pre-baked: K3s, Helm, Docker, full deploy" \
  --no-reboot
```

After the AMI is created, update the launch template in `terraform/main.tf` to use it. Re-bake the AMI after major changes (new Airflow image, Kafka version bump, etc.).

---

## Files changed

| File | Change |
|------|--------|
| `terraform/main.tf` | Replaced single server with launch template + ASG + Lambda + SNS + lifecycle hook; switched to ARM image |
| `terraform/variables.tf` | Default instance type → `t4g.large`, disk size → `30` GiB, added `spot_max_price` variable |
| `terraform/outputs.tf` | Output now references the ASG instead of a single server |
| `terraform/lambda/eip_reassociate.py` | New file — Lambda function that re-assigns the Elastic IP |
| `scripts/deploy.sh` | Dashboard runs in parallel with Kafka/MLflow instead of waiting |
| `scripts/deploy/flask.sh` | Docker build tool URL switched to ARM version |
| `scripts/deploy/terraform.sh` | Removed old single-server references; updated safety check pattern |
| `scripts/bootstrap_ec2.sh` | AWS CLI download URL switched to ARM version; added `--fresh-db` flag to skip backup restore |
| `docs/COSTS.md` | Updated cost tables, added Spot/ASG architecture section |
| `docs/infrastructure/EC2_SIZING.md` | Added t4g.large to sizing tables, ARM compatibility note |

---

## Go-live steps

### Step 1 — Remove old resources from Terraform state (done)

The old single server and its IP association were removed from Terraform's tracking. This tells Terraform to stop managing them — they still exist in AWS and must be cleaned up manually later.

```bash
cd terraform
terraform state rm aws_instance.pipeline
terraform state rm aws_eip_association.pipeline_eip_assoc
```

**Important:** These commands must be run from the `terraform/` directory (where the state file lives), not from the project root. Running from the wrong directory gives "No state file was found!"

The old Elastic IP (`100.30.3.22`) had already been released. The new Elastic IP (`52.70.211.1`) was imported into Terraform state so it doesn't create a duplicate.

### Step 2 — Apply infrastructure changes

```bash
./scripts/deploy/terraform.sh apply
```

This creates the launch template, ASG, Lambda function, SNS topic, lifecycle hook, and all necessary permissions. The ASG immediately launches a new spot ARM server.

**Note on first-time EIP assignment:** The very first time the ASG launches an instance, the Lambda function receives an SNS "subscription confirmation" message instead of the actual lifecycle event. This is a one-time thing — SNS always sends a confirmation when a new subscription is first created. The Lambda sees this as a non-launch event and ignores it, so the Elastic IP does not get assigned automatically on the first launch. To fix this, manually reassign the EIP:

```bash
# Find the new instance's ID
INSTANCE_ID=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names pipeline-asg \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)

# Move the Elastic IP to it
aws ec2 associate-address \
  --instance-id "$INSTANCE_ID" \
  --allocation-id eipalloc-0919f40648b47a2eb \
  --allow-reassociation
```

After this one-time manual fix, all future spot replacements will work automatically — the SNS subscription is already confirmed, so the Lambda will receive the real lifecycle event and reassign the EIP on its own.

### Step 3 — Clean up the old server

After confirming `ssh ec2-stock uname -m` prints `aarch64` (meaning the EIP now points to the new ARM server):

1. **Terminate the old server** — it's still running and costing ~$60/month. In the AWS Console, go to EC2 → Instances, find the old Intel server (`i-04d744aef68debba4`, type t3.large — the one that no longer has the Elastic IP) and terminate it.
2. **Delete the old disk** — the old server's 100 GiB disk survives termination (by design). It costs ~$8/month just sitting there. After the old server finishes terminating, go to EC2 → Elastic Block Store → Volumes, find the orphaned 100 GiB volume (it will show status "available"), and delete it.

### Step 4 — Bootstrap the new ARM server

"Bootstrapping" means setting up a brand-new, empty server so it has all the software needed to run the pipeline. A fresh EC2 instance starts as a bare Ubuntu machine — it doesn't have Kubernetes, Helm, the AWS CLI, a database, or any of the pipeline's configuration. The bootstrap script installs and configures all of that automatically over SSH from your Mac.

Specifically, `bootstrap_ec2.sh` does the following (in order):
1. **Installs system packages** — updates Ubuntu, installs MariaDB, Docker, and other dependencies
2. **Installs the AWS CLI** — so the server can pull container images from ECR
3. **Installs K3s** — the lightweight Kubernetes distribution that runs all the pipeline's containers
4. **Installs Helm** — the package manager used to deploy Airflow into Kubernetes
5. **Sets up the database** — creates the MariaDB database and imports a backup
6. **Configures Kubernetes** — creates namespaces, persistent volumes, storage claims, secrets, and service definitions
7. **Installs Airflow via Helm** — deploys the Airflow chart into the Kubernetes cluster

After this script finishes, the server is ready for a full deploy.

The script requires one argument: the SSH host alias for the server to bootstrap. Since the Elastic IP is now pointing to the new ARM server, use the existing `ec2-stock` alias:

```bash
./scripts/bootstrap_ec2.sh ec2-stock
```

If the old instance was terminated before taking a backup, the script will fail the preflight check. In that case, rerun with `--fresh-db` to skip the backup restore and start with an empty database instead. The DAGs will repopulate it from the source APIs on the next run.

### Step 5 — Full deploy

```bash
./scripts/deploy.sh --provision --snowflake-setup
```

### Step 6 — Verify

```bash
ssh ec2-stock uname -m           # should print: aarch64 (confirms ARM)
ssh ec2-stock kubectl get pods -A # all pods should show Running
```

Load the dashboard at `http://52.70.211.1:32147/dashboard/` and trigger a DAG run in the Airflow UI to confirm everything works end-to-end.

### Step 7 — Bake the AMI

Follow the pre-baked AMI instructions above. This is what makes spot recovery take 3-5 minutes instead of 30-45 minutes.

---

## Gotchas & known issues

### Step 5 fails immediately: "SNOWFLAKE_ACCOUNT is not set in .env.deploy"

**What happened:** Running `./scripts/deploy.sh --provision --snowflake-setup` (Step 5) failed in under a second with:

```
ERROR: SNOWFLAKE_ACCOUNT is not set in .env.deploy — required for --snowflake-setup
```

**Why:** The `.env.deploy` file had the AWS and Flask entries filled in, but the Snowflake block was never added. The `--snowflake-setup` flag needs four credentials to connect to Snowflake and create the pipeline's database objects. Without them, it refuses to run.

**Fix:** Add the four Snowflake variables to `.env.deploy` and fill in your real values. Use single quotes around passwords that contain special characters like `$` or `#` (see next gotcha for why):

```bash
SNOWFLAKE_ACCOUNT="<your-account>"        # e.g. abc12345.us-east-1 — Snowflake UI → bottom-left account menu
SNOWFLAKE_ADMIN_USER="<your-username>"    # your personal Snowflake login (must have SYSADMIN)
SNOWFLAKE_ADMIN_PASSWORD="<your-password>"
SNOWFLAKE_PASSWORD="<new-service-account-password>"  # the password to create for PIPELINE_USER
```

Once all four are filled in, re-run the same command and the Snowflake phase will proceed normally.

### Step 5 fails immediately: "unbound variable" error in .env.deploy

**What happened:** Running `./scripts/deploy.sh --provision --snowflake-setup` failed instantly with:

```
.env.deploy: line 11: Sun: unbound variable
```

**Why:** The password contained a `$` character (e.g. `Bounce19$Sun#32`). When the shell reads a `.env` file, anything inside double quotes that starts with `$` is treated as a variable name — so `$Sun` was interpreted as "look up a variable called `Sun`", which doesn't exist. The shell then stopped and reported the error instead of using the literal password text.

**Fix:** Wrap the password in single quotes instead of double quotes:

```bash
# Wrong — shell tries to expand $Sun as a variable:
SNOWFLAKE_ADMIN_PASSWORD="Bounce19$Sun#32"

# Correct — single quotes tell the shell to use the text exactly as written:
SNOWFLAKE_ADMIN_PASSWORD='Bounce19$Sun#32'
```

Single quotes mean "take everything literally." Double quotes mean "expand variables and special characters." For passwords, single quotes are almost always what you want.

### Step 5 fails immediately: "No module named 'snowflake'"

**What happened:** Running `./scripts/deploy.sh --provision --snowflake-setup` failed with:

```
ModuleNotFoundError: No module named 'snowflake'
```

**Why:** The Snowflake setup step runs a small Python script on your Mac to connect to Snowflake and apply the setup SQL. That script needs a package called `snowflake-connector-python` to be installed. The package is installed inside the server's Python environment (used by the anomaly detector), but it was never installed on the Mac — so when the deploy script tried to run it locally, Python didn't know what `snowflake` was.

**Fix (first attempt):** Added a `pip3 install` line to `scripts/deploy/snowflake.sh` before the Python block. This installed the package successfully, but the error persisted — taking 57 seconds this time instead of instantly, which revealed a subtler problem.

**Why it still failed:** On Macs with multiple Python installations (e.g. Homebrew + system Python + pyenv), `pip3` and `python3` can point to different versions. The package was installed into the Python that `pip3` belongs to, but the script ran under a different `python3` that didn't have it.

**Fix (final):** Changed `pip3 install` to `python3 -m pip install`. The `-m pip` form always installs into the same Python that runs the command — they're guaranteed to be the same interpreter:

```bash
python3 -m pip install --quiet snowflake-connector-python
```

This is safe to run repeatedly — if the package is already installed it does nothing. After this change the deploy proceeds normally.

### Step 5 fails executing SQL: `syntax error line 1 at position 0 unexpected 'written'`

**What happened:** Running `./scripts/deploy.sh --provision --snowflake-setup` connected to Snowflake successfully and started executing SQL, but crashed on the very first statement with:

```
snowflake.connector.errors.ProgrammingError: 001003 (42000): SQL compilation error:
syntax error line 1 at position 0 unexpected 'written'.
```

**Why:** The SQL setup file has a comment near the top of the file that contains a semicolon:

```sql
--   RAW schema      — landing zone; written by Airflow DAGs
```

The Python script split the entire SQL file into individual statements by cutting on every semicolon. It had no idea that this particular semicolon was inside a comment — it just cut the text there. That turned the second half of the comment, `written by Airflow DAGs`, into what looked like its own SQL statement. Snowflake tried to run it, didn't recognise "written" as a valid SQL keyword, and stopped with an error.

**Fix:** Added one line in `scripts/deploy/snowflake.sh` to strip all `--` style comments out of the SQL text before splitting it into statements. That way, any semicolons inside comments are removed along with the comment itself, and only real SQL statement separators remain:

```python
sql_no_comments = re.sub(r'--[^\n]*', '', sql_final)
```

After this, the `split(";")` only ever cuts on real statement boundaries, not on punctuation buried in comments.

### Step 5 fails executing SQL: `Insufficient privileges to operate on database 'PIPELINE_DB'`

**What happened:** Running `./scripts/deploy.sh --provision --snowflake-setup` connected to Snowflake, created the warehouse and database successfully, then crashed on the third statement with:

```
snowflake.connector.errors.ProgrammingError: 003001 (42501): SQL access control error:
Insufficient privileges to operate on database 'PIPELINE_DB'.
```

The script had just created `PIPELINE_DB` in the previous step, so it seemed like it should be able to do anything with it.

**Why:** The script was connecting to Snowflake using a role called `SYSADMIN`. In Snowflake, roles are like permission levels, and `SYSADMIN` is not the top level — `ACCOUNTADMIN` is. `SYSADMIN` has enough permission to create a warehouse and an empty database, but it does not have permission to create schemas inside that database, and it also cannot create new users. The setup script needed to do all of those things, so `SYSADMIN` was the wrong choice for this job. It would have hit permission errors again a few statements later when it tried to create `PIPELINE_USER`.

**Fix:** Changed the connection in `scripts/deploy/snowflake.sh` to use `ACCOUNTADMIN` instead of `SYSADMIN`. `ACCOUNTADMIN` is the top-level role in any Snowflake account and has permission to create and manage everything — databases, schemas, roles, and users. It is the right role for a one-time infrastructure setup script like this one:

```python
role="ACCOUNTADMIN",  # must be ACCOUNTADMIN — SYSADMIN lacks CREATE SCHEMA and CREATE USER privileges
```

### Airflow Docker build fails silently — deploy says "No such image" on import

**What happened:** Running `./scripts/deploy.sh` failed at Step 2b2 with:

```
Error response from daemon: No such image: airflow-dbt:3.1.8-dbt-TIMESTAMP
ctr: unrecognized image format
```

Confusingly, cleanup messages like "Pruning old airflow-dbt Docker images..." appeared in the output even though the build supposedly failed, making it look like the build had succeeded.

**Why:** The build step and the cleanup steps were chained together in one command using `&&` and `|| true`. In the shell, `&&` and `||` have equal priority and are read from left to right, so a line like `docker build && cleanup_cmd || true && next_step` is actually grouped as `((docker build && cleanup_cmd) || true) && next_step`. When `docker build` fails, the `|| true` at the end of the cleanup command makes the whole left side count as a success, and the next steps keep running. The cleanup echoes appeared because they came after the `|| true` took effect. The whole first connection to the server exited cleanly with a success code even though the build never finished, so the deploy continued and tried to import an image that didn't exist.

**Fix:** Moved the cleanup block into a subshell placed after `docker build &&`. A subshell is a group of commands in parentheses: `docker build && ( cleanup commands... )`. If the build fails, the `&&` before the subshell stops execution immediately — the subshell never runs and the server connection exits with the build's failure code. The deploy now sees the failure right away instead of getting confused by the cleanup messages.

Also added `DOCKER_BUILDKIT=1` to the build command to use Docker's modern build engine instead of the older one (which was showing a deprecation warning). This makes the Airflow build behave the same way as the Flask build.

### Terminal appears frozen for several minutes after a deploy failure

**What happened:** After the deploy failed and printed the error summary, the terminal appeared to hang for 5+ minutes with no output and no prompt. Pressing keys did nothing visible.

**Why:** The deploy script runs several tasks at the same time in the background — building the Airflow image, setting up Kafka, setting up MLflow, and building the Flask container. When the Airflow build failed, the deploy printed the failure summary and exited. But the Kafka and MLflow background tasks were still running, waiting for their containers to finish starting up. Kafka in particular can take 7–10 minutes.

The deploy script also routes all output through a logging process (using `tee`). That logging process stays alive as long as any of the background tasks are still running. While the logging process is alive, it holds the terminal's output channel open, so the shell prompt doesn't fully reappear and the terminal looks frozen. No output appears because the background tasks are silently waiting for pods, not printing anything.

**Fix:** The deploy script now kills any background tasks that are still running as part of its cleanup when it detects a failure. This causes the logging process to close immediately, returning the terminal within a second or two of the failure message.

**If you hit this on an older version:** Press Ctrl+C to interrupt the terminal and get your prompt back. The background tasks (Kafka, MLflow) had already submitted their work to the server before dying, so Kafka and MLflow will still start up on EC2 — you just won't see the confirmation output. Re-run the deploy once you have fixed the underlying error.

### Deploy fails immediately after Terraform: `Connection closed by 52.70.211.1 port 22`

**What happened:** Running `./scripts/deploy.sh --provision --snowflake-setup` failed right at Step 1 with:

```
Warning: Permanently added '52.70.211.1' (ED25519) to the known hosts.
Connection closed by 52.70.211.1 port 22

  DEPLOY FAILED  (exit code: 255)
```

Running `ssh ec2-stock echo ok` manually a moment later returned `ok` without any issues.

**Why:** The `--provision` flag runs Terraform before the deploy starts. Terraform updates the firewall rule that controls which IP address is allowed to SSH into the server. After that update is submitted to AWS, there is a brief window — typically just a few seconds — while the new rule is being applied across AWS's internal networking. During this window the server accepted the initial connection (which is why you see "Permanently added" instead of "Connection refused"), but then immediately closed it before the login could complete. The deploy script tried to connect right in the middle of that window and got cut off.

**Fix:** Added a small function (`_wait_ssh_ready`) to `scripts/deploy/common.sh` that tests the connection before the first real command runs. If the first attempt fails, it retries up to five times with a five-second pause between each try (25 seconds maximum total wait). The function is called at the very start of Step 1 in `scripts/deploy/setup.sh`. On a working server this adds zero delay — it succeeds on the first attempt and moves on immediately.

### Step 2d times out on a fresh install: `failed post-install: 1 error occurred: * timed out waiting for the condition`

**What happened:** Running `./scripts/deploy.sh --provision --snowflake-setup` on the new server got through the Airflow Docker build successfully, then Step 2d printed "Release 'airflow' does not exist. Installing it now." and hung for exactly 10 minutes before failing with:

```
Error: failed post-install: 1 error occurred:
    * timed out waiting for the condition
```

**Why:** When Helm installs Airflow for the first time, it runs two one-time setup jobs: one to prepare the database (the "migrate" job) and one to create the admin login account (the "create user" job). By default both jobs run as Helm "hooks" — meaning Helm waits for them to finish before declaring the install a success.

The migrate job had already been switched to run in the background (the `migrateDatabaseJob.useHelmHooks: false` setting in `values.yaml`), so Helm no longer waits for it. But the create-user job was never given the same treatment — it still ran as a hook.

The problem is that the create-user job cannot start doing its work until the database migration is completely finished. So it sits and waits. On a fresh server, the database migration can take well over 10 minutes. Helm's timeout is set to 10 minutes. Helm gives up and reports a failure, even though the migration was still running fine in the background.

**Fix:** Added `createUserJob.useHelmHooks: false` to `airflow/helm/values.yaml`, right alongside the existing setting for the migrate job. With this change, Helm creates both jobs and then returns immediately without waiting for either of them. The jobs still run and finish in the background — the create-user job still waits for the migration before it does anything — but Helm no longer sits there watching the clock. Step 2d now completes in a few seconds instead of timing out.

After a successful deploy, you can confirm both jobs finished by running:
```bash
ssh ec2-stock kubectl get jobs -n airflow-my-namespace
```
Both the migrate and create-user jobs should show `1/1` under the COMPLETIONS column.

### Step 5 fails at Step 2d: `UPGRADE FAILED: "airflow" has no deployed releases`

**What happened:** Running `./scripts/deploy.sh --provision --snowflake-setup` on the new server got through the Airflow Docker build successfully, then failed at Step 2d with:

```
Error: UPGRADE FAILED: "airflow" has no deployed releases
```

**Why:** The deploy script's Step 2d runs `helm upgrade`, which tells Helm to update an already-installed Airflow. On a brand-new server, Airflow has never been installed, so there is nothing to upgrade. Helm refuses and exits with this error.

Airflow is supposed to be installed during the bootstrap step (`bootstrap_ec2.sh`, Phase E). The bootstrap does run `helm install`, but it has a safety net written as `|| echo 'NOTE: ...'`. This means: "if `helm install` fails for any reason, print a note and continue." The problem is that this swallows any real failure silently — the bootstrap script finishes, reports success, and the deploy script runs `helm upgrade` against a server where Airflow was never actually installed.

**Fix:** Added `--install` to the `helm upgrade` command in `scripts/deploy/airflow_pods.sh`. With this flag, Helm installs Airflow from scratch if it has never been installed, or upgrades it if it already exists. The result is the same either way — the correct version of Airflow ends up running. This is the standard way to write a Helm deploy that works on both fresh servers and existing ones.

```bash
helm upgrade --install airflow apache-airflow/airflow ...
```

### Step 5 fails in Python with: `KeyError: 'SNOWFLAKE_PASSWORD'`

**What happened:** Running `./scripts/deploy.sh --provision --snowflake-setup` failed inside the Snowflake setup step with:

```
File "<stdin>", line 7, in <module>
KeyError: 'SNOWFLAKE_PASSWORD'
```

**Why:** The deploy script loads credentials from `.env.deploy` using the shell's `source` command. A plain `source` makes variables available to the current shell script and its functions, but does not pass them on to programs the script launches — like the Python process that connects to Snowflake. The check that validates whether `SNOWFLAKE_PASSWORD` is set runs inside the shell (so it passes), but by the time Python starts as a separate process, it has no access to those variables and cannot find `SNOWFLAKE_PASSWORD` anywhere.

**Fix:** Changed the `source` line in `scripts/deploy/common.sh` to wrap it with `set -a` and `set +a`:

```bash
set -a; source "$ENV_DEPLOY"; set +a
```

`set -a` tells the shell to automatically mark every variable it sets as available to child processes. Wrapping the `source` call with it means every credential from `.env.deploy` — AWS, Flask, and Snowflake — is automatically passed through to Python and any other program the deploy script runs.

### Step 2d fails on second and later deploys: `UPGRADE FAILED: Job "airflow-create-user" exists and cannot be imported`

**What happened:** Running `./scripts/deploy.sh` on any deploy after the first one failed at Step 2d with:

```
Error: UPGRADE FAILED: Unable to continue with update: Job "airflow-create-user" in namespace "airflow-my-namespace" exists and cannot be imported into the current release: invalid ownership metadata; label validation error: missing key "app.kubernetes.io/managed-by": must be set to "Helm"; annotation validation error: missing key "meta.helm.sh/release-name": must be set to "airflow"; annotation validation error: missing key "meta.helm.sh/release-namespace": must be set to "airflow-my-namespace"
```

**Why:** The previous fix added `createUserJob.useHelmHooks: false` to `values.yaml` so that Helm would not sit and wait for the create-user job to finish. This setting has a side effect: when Helm creates the job, it does not add the three ownership labels that Helm uses to track resources it manages. On the very first install this is fine — the job does not exist yet, so there is nothing to conflict with. But once the job has been created, every subsequent `helm upgrade` sees the existing job sitting in Kubernetes, tries to take ownership of it, notices the missing labels, and refuses to continue.

The job itself is a one-time task — all it does is create the Airflow admin account. Once that account exists, re-running the job is harmless (it just finds the account already there and does nothing). So the job can be safely deleted before each upgrade; Helm will recreate it, and it will finish in the background without causing any problems.

The `migrateDatabaseJob` has the same `useHelmHooks: false` setting and is exposed to the identical conflict, so it is cleaned up at the same time as a precaution.

**Fix:** Added two lines to `step_helm_upgrade()` in `scripts/deploy/airflow_pods.sh` that delete both one-time jobs before `helm upgrade` runs. The `--ignore-not-found=true` flag makes the delete a no-op on a fresh server where the jobs have not been created yet, so this is safe to run every time regardless of state:

```bash
kubectl delete job airflow-create-user airflow-run-airflow-migrations \
    -n airflow-my-namespace --ignore-not-found=true
```

### Step 7 freezes after pod restart, then all pods time out

**What happened:** Running `./scripts/deploy.sh` on the new ARM server appeared to freeze
during Step 7. The output showed timeout errors for the dag-processor and triggerer pods,
then went silent for several minutes before eventually printing a timeout error for the
scheduler too, then exiting with a failure after 22 minutes total.

**Why:** Two separate problems happened at the same time.

*Why the terminal froze:* When Step 7 starts waiting for the three Airflow pods to come
back up, it runs all three waits at the same time in the background. The scheduler wait
is set to 1,000 seconds, while the other two are set to 600 seconds. The script then
checks the results — but it checked the scheduler first. The dag-processor and triggerer
had already printed their timeout messages and finished at 600 seconds, but bash was still
sitting inside the scheduler wait, which had 400 more seconds on the clock. The terminal
looked frozen because nothing was printing, but the script was alive, just waiting for the
wrong thing.

*Why there were two dag-processor pods from two different versions:* Helm's rolling update
from earlier in the deploy (Step 2d) had not fully finished by the time Step 7 ran. When
a Kubernetes Deployment does a rolling update, it briefly keeps pods from both the old
version and the new version alive at the same time. The deploy script deleted all pods
matching the dag-processor label — one from the old version, one from the new. Kubernetes
then created replacements for both. But Kubernetes was also still trying to scale the
old-version pod down to zero as part of the rolling update. That old pod was being created
and immediately scheduled for deletion at the same time — it could never reach a stable
running state, and the 600-second wait ran out before it settled.

**Fix:** Two changes were made to `scripts/deploy/airflow_pods.sh`.

First, a rollout status check was added before any pods are deleted. The script now waits
(up to 2 minutes) for the dag-processor Deployment's rolling update to fully complete
before touching any pods. Once only one version is active, deletion and replacement work
cleanly.

Second, the order in which the script checks wait results was changed. The dag-processor
and triggerer (600-second waits) are now checked before the scheduler (1,000-second wait).
If either fails, the script immediately stops the still-running scheduler wait and exits —
instead of sitting silently for up to 400 more seconds. Each failure now also automatically
prints a pod description to show why the pod was not ready, so you do not need to SSH in
manually to diagnose it.

### Deploy fails immediately: ".env.deploy: line N: <value>: No such file or directory"

**What happened:** Running `./scripts/deploy.sh` failed instantly with:

```
.env.deploy: line 20: KJ8VXmZucER6PJFLEaaVC/kIoW1zB0C2: No such file or directory
```

**Why:** When the shell reads `.env.deploy`, it processes each line as a statement. A line that looks like `VARNAME=value` sets a variable. A line that looks like `some/path` (no `=`) is treated as a command to run — the shell tries to execute it as if it were a script or program. When it can't find that file, it reports "No such file or directory."

This happens when the variable name and its value end up on separate lines. For example, if you accidentally press Enter in the middle of a line while editing the file:

```bash
# Wrong — value ends up on its own line, shell tries to run it as a command:
FLASK_SECRET_KEY=
KJ8VXmZucER6PJFLEaaVC/kIoW1zB0C2
```

**Fix:** Open `.env.deploy`, find the lines around the line number in the error, and make sure the variable name and value are on the same line. Wrap the value in single quotes:

```bash
# Correct — everything on one line; single quotes protect any special characters:
FLASK_SECRET_KEY='KJ8VXmZucER6PJFLEaaVC/kIoW1zB0C2'
```

Single quotes work for any value — they tell the shell to use the text exactly as written, with no special meaning for any character inside.

---

### Step 7 fails with ErrImageNeverPull on a stale dag-processor pod

**What happened:** Running `./scripts/deploy.sh` failed at Step 7 with:

```
error: timed out waiting for the condition on pods/airflow-triggerer-0
timed out waiting for the condition on pods/airflow-dag-processor-6d5b59f869-2m6q6
timed out waiting for the condition on pods/airflow-dag-processor-d8659b769-7qz8x
✗ dag-processor Ready FAILED — describing pods...
Warning  ErrImageNeverPull  ...  Container image "airflow-dbt:3.1.8-dbt-<old-timestamp>" is not present with pull policy of Never
```

Two dag-processor pods appeared — one from the new (current) version, one from an older version that was supposed to have been shut down. The older pod was trying to use an image that no longer existed.

**Why:** Step 7 restarts pods by deleting everything with the `component=dag-processor` label. That label matches pods from *any* version of the deployment — both the new one and any old one still in the process of shutting down.

Before deleting, the script waits for Helm's rolling update to finish using `kubectl rollout status`. But that command had a 2-minute time limit, and the rolling update was still in progress when the limit ran out. A `|| true` guard silently swallowed the timeout and let the script continue.

At that point, the older version of the deployment still had a "desired count" of 1 — meaning Kubernetes thought it should be running exactly one pod of that old version. When Step 7 deleted the old pod, Kubernetes immediately created a fresh replacement to satisfy that count. That replacement tried to use the old image, which had already been cleaned up during the current build — resulting in the "image not present" error.

The triggerer pod failure was a cascading effect: it was waiting for a database setup step to finish, and the extra load from the misbehaving old pod caused it to time out too.

**Fix:** Two changes to `scripts/deploy/airflow_pods.sh`:

First, the rollout wait timeout was increased from 2 minutes to 5 minutes, giving the rolling update more time to complete cleanly.

Second, a new polling step was added after the rollout wait. Even when a rolling update finishes, the old pod can take another 30–60 seconds to fully stop. The new poll checks how many dag-processor pods are currently active and waits until there's exactly 1. Once there's only 1 pod, the script knows there's only one version running and it's safe to proceed with the restart.

### Step 7 fails: ErrImageNeverPull on old pod and triggerer/dag-processor stuck at Init:0/1

**What happened:** Running `./scripts/deploy.sh` on the new ARM server printed the 5-minute warning about two dag-processor pods, continued, and then failed at Step 7 with:

```
airflow-dag-processor-6d5b59f869-579x2   0/2     Init:ErrImageNeverPull
airflow-dag-processor-cd5dc8499-64sqz    0/2     Init:0/1
timed out waiting for the condition on pods/airflow-dag-processor-cd5dc8499-64sqz
timed out waiting for the condition on pods/airflow-triggerer-0
```

**Why:** Two problems compounded each other.

*Why the init containers kept cycling:* Before the main Airflow containers start, each pod runs a small startup check called an init container — it verifies the database setup is finished before allowing the rest of the pod to start. That init container uses the same Docker image as the rest of Airflow, which means it also picks up a setting called `_PIP_ADDITIONAL_REQUIREMENTS` that was listed in `values.yaml`. At pod startup, the Airflow image's own entrypoint script reads that setting and downloads and installs any listed packages before doing anything else. Five packages were listed, and on the new ARM server, downloading and installing them took 5–7 minutes — longer than the 5-minute window the deploy script allows for the rolling update to finish. Because the init containers couldn't complete in time, the rolling update never finished.

*Why the old pod got ErrImageNeverPull:* Because the rolling update never finished, both the old and new version of the dag-processor were still running when Step 7 deleted all matching pods. Kubernetes immediately tried to create replacement pods for both versions. The old version's replacement needed the old Docker image — but that image had already been cleaned up during the build step earlier in the deploy. The replacement pod looked for a local copy that was no longer there and failed with "image not present."

**Fix:** Two changes.

First, the five packages (`pymysql`, `requests>=2.32.3`, `apache-airflow-providers-common-compat>=1.5.0`, `kafka-python`, `python-dotenv`) were moved from `_PIP_ADDITIONAL_REQUIREMENTS` in `values.yaml` into the Docker image itself (`airflow/docker/Dockerfile`). They are now installed once when the image is built, so there is nothing left to download at pod startup. The init container finishes its database check in under 30 seconds and the rolling update completes well before the 5-minute window closes. The `_PIP_ADDITIONAL_REQUIREMENTS` entry was removed from `values.yaml`.

Second, a safety net was added to `scripts/deploy/airflow_pods.sh` for cases where the 5-minute poll still times out. When that happens, the script now finds the older of the two ReplicaSets and sets its desired pod count to zero before deleting any pods. With its count at zero, the old controller no longer creates a replacement pod — so the "missing image" error cannot occur.

### Step 7 fails: dag-processor and triggerer stuck at Init:0/1 (wait-for-airflow-migrations never passes)

**What happened:** Running `./scripts/deploy.sh` on the spot instance failed at Step 7 with both the dag-processor and triggerer pods permanently stuck:

```
airflow-dag-processor-84fb98c88d-sbfsr   0/2     Init:0/1   1 (4m42s ago)   10m
airflow-triggerer-0                      0/2     Init:0/1   1 (2m49s ago)   10m
```

The pods' init container (`wait-for-airflow-migrations`) ran for its full 300-second timeout, was restarted by Kubernetes, ran for another 300 seconds, and was still waiting when the deploy script gave up at 600 seconds.

**Why:** Step 2f (added earlier to wait for the database migration job before restarting pods) had a bug that allowed the deploy to continue even when the migration job had not finished.

The migration wait runs inside an SSH command on the server. On the server, the shell does not have "stop on error" turned on — so when one command fails, the next command still runs. Step 2f looked like this:

```bash
kubectl wait job/airflow-run-airflow-migrations ... --timeout=600s
echo 'Migration job complete.'
```

When the `kubectl wait` timed out (returned an error), the shell moved on to the `echo` line, which always succeeds. The SSH session reported "success" back to the deploy script. The deploy script believed migrations were done and continued to Step 7.

At Step 7, all Airflow pods were deleted and recreated. Each new pod's init container tried to verify the database schema — but the schema still was not ready (because the migration job never finished). The init containers waited 300 seconds each attempt, timed out, restarted, and timed out again. The deploy script's 600-second wait ran out and the deploy failed.

**Fix:** Two changes to `scripts/deploy/airflow_pods.sh`.

First, the migration wait in Step 2f was fixed so that a timeout actually stops the deploy. Adding `|| exit 1` after the `kubectl wait` command tells the server's shell to stop immediately if the wait fails — the `echo` line never runs, so it cannot mask the error:

```bash
kubectl wait job/airflow-run-airflow-migrations ... --timeout=600s || exit 1
echo 'Migration job complete.'
```

With this fix, a migration timeout causes the deploy to fail at Step 2f with a clear error, instead of silently continuing and failing 15 minutes later at Step 7 with a confusing `Init:0/1` message.

Second, a safety-net check was added at the very start of Step 7. Before deleting any pods, the script now checks whether the migration job still exists and whether it has completed. If the job is still running, the script waits up to 300 seconds for it. If the job is already done (or doesn't exist), the check returns instantly and adds no delay. This catches two edge cases that the Step 2f fix alone cannot:

- When running with `--dags-only`, Step 2f is skipped entirely (Helm upgrade doesn't run in that mode), so there is no migration wait at all
- If the database was disrupted between Step 2f and Step 7 (for example, PostgreSQL restarting under memory pressure on a busy server)

---

## Lessons learned

1. **Spot instances cut costs by 75% with almost no downside** for workloads that handle restarts gracefully. The ASG + Lambda approach keeps the server self-healing automatically.

2. **A load balancer was unnecessary here.** It would have cost ~$16/month for a single-server setup. The Lambda + Elastic IP combo does the same job for free.

3. **ARM was a drop-in swap.** Every container image used by the pipeline already supports ARM. No code or Dockerfile changes were needed — just changing the instance type and a few download URLs.

4. **The dashboard had no reason to wait for Kafka.** The deploy script was running things in order out of habit, not because of a real dependency. Fixing this cut 10+ minutes off every deploy.

5. **Always run `terraform` commands from the right directory.** The state file lives in `terraform/`, not the project root. Running from the wrong directory gives a confusing "No state file was found!" error.

6. **Removing something from Terraform state does not delete it from AWS.** It just means Terraform stops tracking it. The old server and its disk had to be cleaned up by hand.

7. **Take the database backup before terminating the old instance.** Once the instance is gone, the backup is gone too. The `--fresh-db` flag exists as a recovery path, but it means re-running all DAGs to repopulate the data.

---

**Date:** 2026-04-13
**Affected component:** EC2 instance, Terraform config, deploy scripts
**Data lost:** None
