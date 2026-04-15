# AWS SSO Login

How to log back in to AWS when your session has expired.

---

## What Is AWS SSO?

AWS SSO (Single Sign-On) is the authentication system used to access AWS resources from your
terminal. It works like a timed badge — it grants access for a set period (typically 8 hours),
and then expires. When it expires, any command that talks to AWS will fail silently or return
an error until you log in again.

---

## Signs Your Session Has Expired

- AWS CLI commands return errors like `The SSO session associated with this profile has expired`
- Scripts that query AWS (e.g., `./scripts/deploy/ami.sh status`) return no results or
  fallback messages like "Could not determine current AMI"

---

## How to Log In

Run this command in your terminal:

```bash
aws sso login --profile terraform-dev
```

A browser window will open asking you to confirm the login. Click **Allow**. Once confirmed,
your terminal session will have access to AWS again.

**General form** (for any project):

```bash
aws sso login --profile <your-profile-name>
```

The profile name comes from your `~/.aws/config` file. Each project or AWS account may use a
different profile name.

---

## After Logging In

You do not need to restart your terminal. Re-run whatever command failed — it will work now.

```bash
./scripts/deploy/ami.sh status
```

---

## How Long Does It Last?

Sessions typically last **8 hours**. You will need to log in again after that, or whenever
you see the expired session error.
