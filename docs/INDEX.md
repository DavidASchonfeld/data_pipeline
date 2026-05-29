# Documentation

Navigate all project documentation from here. Each link goes to the single source of truth for that topic.

---

## Start Here

| Document | What it covers |
|----------|---------------|
| [README](../README.md) | What this project does, architecture, tech stack, design decisions |
| [Setup Guide](../SETUP.md) | Local development, production deployment, how code flows from Mac to EC2 to pods |
| [Deploy Guide](DEPLOY_GUIDE.md) | How `deploy.sh` works, Terraform, and how to restore the project after shutdown |
| [Costs](COSTS.md) | Monthly cost breakdown per service, cost controls, shutdown and restoration |

---

## Guides

| Document | What it covers |
|----------|---------------|
| [Data Source Guide](DATA_SOURCE_GUIDE.md) | How to add, edit, remove, or replace a data source (API → Kafka → Snowflake → dbt → Dashboard) |
| [GenAI Overview](GENAI_OVERVIEW.md) | Plain-English explanation of the AI layer: what it will do and what's built so far |

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
| [Spot Recovery](architecture/SPOT_RECOVERY.md) | How the server automatically recovers when AWS reclaims a spot instance |
| [Alerting](architecture/ALERTING.md) | Failure alerts, staleness monitoring, Slack webhooks, cooldown system |
| [GenAI Data Separation](architecture/GENAI_DATA_SEPARATION.md) | How subjects, sources, and projects are kept separate (and together): namespace vs metadata vs database; one chatbot for all subjects; what copies when ported |

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
| [Automated Tests](infrastructure/CICD_TESTS.md) | What the CI checks do, when they run, how to read results, cost impact |

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
| [Data Timing](reference/DATA_TIMING.md) | How long after triggering a DAG until data appears on the dashboard |
| [GenAI / RAG References](reference/GENAI_RAG_REFERENCES.md) | Annotated external best-practice links behind the GenAI design: deletions/tombstones, embedding versioning, chunking, eval, observability, multi-tenant security |

---

## Decisions & Licensing

| Document | What it covers |
|----------|---------------|
| [Architecture Decisions](decisions/README.md) | Why each major design choice was made (ADRs 0001–0005), in plain language |
| [Third-Party Notices](../THIRD_PARTY_NOTICES.md) | Every third-party library, image, dataset, and API used, with its license — the project's attribution and proof-of-right-to-use record |
| [License Texts](../licenses/README.md) | Full upstream license texts for the components listed in the notices file |

---

## Common Questions

| Question | Go to |
|----------|-------|
| How do I add a new data source? | [DATA_SOURCE_GUIDE.md](DATA_SOURCE_GUIDE.md) |
| Do stocks and weather (or two sources, or another project) need to be kept separate? | [architecture/GENAI_DATA_SEPARATION.md](architecture/GENAI_DATA_SEPARATION.md) |
| How do I deploy code changes? | [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md) |
| How much does this cost per month? | [COSTS.md](COSTS.md) |
| How do I verify everything works after a deploy? | [VERIFICATION.md](VERIFICATION.md) |
| Something broke — how do I debug it? | [operations/TROUBLESHOOTING.md](operations/TROUBLESHOOTING.md) |
| What can go wrong with my pipeline? | [architecture/FAILURE_MODE_MAP.md](architecture/FAILURE_MODE_MAP.md) |
| What happened in the past? (incident history) | [incidents/INDEX.md](incidents/INDEX.md) |
| How do I set up Snowflake? | [operations/runbooks/setup-snowflake.md](operations/runbooks/setup-snowflake.md) |
| How does alerting work? | [architecture/ALERTING.md](architecture/ALERTING.md) |
| How do PersistentVolumes work? | [infrastructure/PERSISTENCE.md](infrastructure/PERSISTENCE.md) |
| What does a term mean? | [reference/GLOSSARY.md](reference/GLOSSARY.md) |
| What third-party tools/licenses does this use? | [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) |
| Why was a design choice made this way? | [decisions/README.md](decisions/README.md) |
