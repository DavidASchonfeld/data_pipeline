# Documentation

Navigate all project documentation from here. Each link goes to the single source of truth for that topic.

---

## Start Here

| Document | What it covers |
|----------|---------------|
| [README](../README.md) | What this project does, architecture, tech stack, design decisions |
| [Setup Guide](../SETUP.md) | Local development, production deployment, how code flows from Mac to EC2 to pods |
| [Deploy Guide](DEPLOY.md) | How `deploy.sh` works, Terraform, and how to restore the project after shutdown |
| [Costs](COSTS.md) | Monthly cost breakdown per service, cost controls, shutdown and restoration |

---

## Verify Everything Works

| Document | What it covers |
|----------|---------------|
| [Verification Checklist](VERIFICATION.md) | 14-step post-deploy validation: pods, Snowflake, Kafka, dbt, pipelines, MLflow, dashboard |

---

## Architecture

| Document | What it covers |
|----------|---------------|
| [System Overview](architecture/SYSTEM_OVERVIEW.md) | K3s, containerd, pods, services, PVs, ETL data flow, Helm |
| [Data Flow](architecture/DATA_FLOW.md) | Validation gates at each pipeline stage, XCom transport risks |
| [Component Interactions](architecture/COMPONENT_INTERACTIONS.md) | Dependency graph, blast radius analysis, cascade failure chains |
| [Failure Mode Map](architecture/FAILURE_MODE_MAP.md) | What can go wrong per component: symptoms, root causes, diagnostic rules |
| [Dashboard Cache](architecture/DASHBOARD_CACHE.md) | How the query cache works, why it saves Snowflake costs |
| [Alerting](architecture/ALERTING.md) | Failure alerts, staleness monitoring, Slack webhooks, cooldown system |

---

## Operations

| Document | What it covers |
|----------|---------------|
| [Quick Reference](operations/QUICK_REFERENCE.md) | Common commands: deploy, check pods, trigger DAGs, access UIs |
| [Runbooks](operations/RUNBOOKS.md) | Step-by-step playbooks for 18 operational tasks |
| [Troubleshooting](operations/TROUBLESHOOTING.md) | Debugging approach, diagnostic sequences, specific issue solutions |
| [Prevention Checklist](operations/PREVENTION_CHECKLIST.md) | Pre-deploy, post-deploy, weekly health, DAG authoring checklists |

---

## Infrastructure

| Document | What it covers |
|----------|---------------|
| [EC2 Sizing](infrastructure/EC2_SIZING.md) | Instance sizing, RAM breakdown, resource limits, when to resize |
| [K3S Risks](infrastructure/K3S_RISKS.md) | Single-node tradeoffs, containerd, Helm state, security |
| [Persistence](infrastructure/PERSISTENCE.md) | PV/PVC deep dive: hostPath risks, filesystem cache, recovery |
| [ECR Setup](infrastructure/ECR_SETUP.md) | AWS ECR configuration, image push workflow |

---

## Incidents

| Document | What it covers |
|----------|---------------|
| [Incident Log](incidents/INDEX.md) | All past issues organized by component, with links to individual write-ups |

---

## Reference

| Document | What it covers |
|----------|---------------|
| [Shell Commands](reference/COMMANDS.md) | What `ss`, `rsync`, `kubectl`, `docker` commands do |
| [kubectl Commands](reference/KUBECTL_COMMANDS.md) | Kubernetes CLI reference |
| [Glossary](reference/GLOSSARY.md) | Technical terms: ETL, DAG, PV, PVC, K3S, XCom, etc. |

---

## Roadmap

| Document | What it covers |
|----------|---------------|
| [Backlog](BACKLOG.md) | Ordered task checklist: what's done, what's next |

---

## Common Questions

| Question | Go to |
|----------|-------|
| How do I deploy code changes? | [DEPLOY.md](DEPLOY.md) |
| How much does this cost per month? | [COSTS.md](COSTS.md) |
| How do I verify everything works after a deploy? | [VERIFICATION.md](VERIFICATION.md) |
| Something broke — how do I debug it? | [operations/TROUBLESHOOTING.md](operations/TROUBLESHOOTING.md) |
| What can go wrong with my pipeline? | [architecture/FAILURE_MODE_MAP.md](architecture/FAILURE_MODE_MAP.md) |
| What happened in the past? (incident history) | [incidents/INDEX.md](incidents/INDEX.md) |
| How do I set up Snowflake? | [operations/runbooks/setup-snowflake.md](operations/runbooks/setup-snowflake.md) |
| How does alerting work? | [architecture/ALERTING.md](architecture/ALERTING.md) |
| How do PersistentVolumes work? | [infrastructure/PERSISTENCE.md](infrastructure/PERSISTENCE.md) |
| What does a term mean? | [reference/GLOSSARY.md](reference/GLOSSARY.md) |
