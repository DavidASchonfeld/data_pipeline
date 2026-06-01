# Incident: The Embedding Model's Weights Weren't Baked Into the Image — Every Pod Restart Re-Downloaded Them From the Internet

**Date:** 2026-06-01
**Severity:** Low (a warning + a latent risk, never an outage — but it put an external download in the request path)
**Status:** Resolved — model weights baked into the Docker image; runtime set to "offline" so it never contacts the internet

---

## Summary (the one-paragraph version)

While running the final checks on the new AI question-answering feature, I noticed this line in the output: `Warning: You are sending unauthenticated requests to the HF Hub.` It looked alarming — the whole point of this setup is that the AI model runs **on my own server**, so why is it talking to the internet at all? The answer turned out to be a subtle but important distinction: the model **runs** locally, but the model's **weights** — the actual ~90 MB file of learned numbers that makes it work — were never stored on my server permanently. They were being downloaded fresh from a public website (Hugging Face) the first time the server needed them, and because that download lands in a temporary folder that gets wiped every time the container restarts, it happened **again after every restart**. Nothing was broken, but it meant an outside download was quietly sitting in the middle of answering a question — and if that website were ever down or slow, answering questions would break. The fix: download the weights once when building the server image, store them permanently inside it, and tell the software "never phone home again."

---

## Background: what "the model" and "the weights" actually are

This is the heart of the confusion, so it's worth slowing down. People say "the AI model" as if it's one thing. It's really **two** things, and they're stored in completely different places.

### 1. The model (the structure / the recipe)

The "model" is the **shape** of the machine — the arrangement of steps that text flows through to come out the other end as numbers. Think of it as a giant, empty spreadsheet where all the formulas are written in, but every cell that should hold a number is blank. Or think of it as an empty brain: all the wiring is there, but none of the connections have any strength yet.

This part is **small** and comes bundled inside a normal piece of software — a Python library called `sentence-transformers` that gets installed with everything else. It's just code. It describes *how* to do the math, but on its own it can't actually do anything useful, because all the important numbers are missing.

### 2. The weights (the learned numbers that fill the structure)

The "weights" are the **millions of specific numbers** that get poured into that empty structure. They are the result of **training** — the slow, expensive process where the model was shown enormous amounts of text and gradually adjusted every one of those numbers until it got good at its job. For the model used here (`all-MiniLM-L6-v2`), that's a single file of about **90 megabytes** of nothing but numbers.

The weights are what make the model *this particular* model instead of a blank one. Same empty spreadsheet + a different set of numbers = a completely different AI. The structure is generic; the weights are the actual learned knowledge.

### Why the model and the weights are stored separately

Here's the key part:

- **The structure (code)** is tiny and generic — the same `sentence-transformers` library can run hundreds of different models. So it ships inside the software you install, like a DVD player that works with any disc.
- **The weights** are big, and there's a different set for every model. Bundling every model's 90+ MB of weights into the library would make it enormous and mostly wasted. So the weights are hosted **separately**, on a public library-of-models website called the **Hugging Face Hub**, and downloaded only when you ask for that specific model by name — like renting just the one disc you actually want to watch.

So when the code says `SentenceTransformer("all-MiniLM-L6-v2")`, that string is a **name**, not a file on the server. The library reads it as: "go find the weights called `all-MiniLM-L6-v2`, download them if they're not already here, then load them into the empty structure." The *running* happens locally; the *getting the weights* is a one-time fetch from the internet.

**The short version:** the model is the empty machine, the weights are the fuel. The machine was installed properly. The fuel was being trucked in fresh from outside every time the engine restarted, instead of being kept in a tank on-site.

---

## Going deeper: are the model and the weights the same thing? And how does this compare to ChatGPT or Claude?

This deserves a fuller answer, because it explains why this whole pipeline is built the way it is.

### Are the model and the weights the same thing?

**No — but in everyday talk people blur them**, because the weights are the part you actually download. To be precise, a model is two pieces:

- **The architecture** — the *shape* of the machine: how many layers, how they connect, what math happens in what order. It's a fixed blueprint defined by code, and it's small. On its own it's an empty shell: feed text in and you get **gibberish** out, because none of the numbers that do the real work have meaningful values yet.
- **The weights (also called parameters)** — the **millions or billions of specific numbers** slotted into that blueprint. A programmer does not write these; they are *discovered* through training.

Same blueprint + a different set of weights = a completely different AI. The architecture gives the *capacity*; the weights are the *filled-in knowledge*.

### What makes the model "understand" or "learn"?

**Learning is the process of setting the weights.** It goes like this:

1. Start with random weights — the model knows nothing, pure gibberish.
2. Show it a piece of text and have it make a prediction.
3. Compare the prediction to the right answer. When it's wrong, an algorithm nudges **every weight** a tiny bit in the direction that would have made the answer better.
4. Repeat billions of times over enormous amounts of text.

Slowly, those billions of tiny nudges settle the weights into a configuration that captures real patterns — which words relate to which, how meaning combines, what tends to follow what. **That captured pattern *is* the knowledge, and it lives entirely in the values of the weights.** The architecture is just a set of knobs; training is what turns the knobs; the weights are the final knob positions.

So the model does not "understand" the way a person does — it has learned, from statistics, how language fits together well enough to be genuinely useful. For *this* model (`all-MiniLM-L6-v2`), what it learned is how to turn a sentence into a point in a 384-dimensional space where similar meanings land close together — exactly what powers "search by meaning."

**An analogy:** the architecture is a piano — the instrument, the same for everyone. The weights are the precise muscle memory a pianist builds over years of practice. The piano alone makes no music; the practice (training) produces the skill; and that skill is "stored" in the learned finger patterns (the weights). Hand the same piano to a beginner (random weights) and you get noise.

### How does this compare to ChatGPT or Claude? Are their "powers" in the weights?

**Yes — almost entirely**, and the idea scales all the way up:

- The small embedding model here and a giant chat model are built from the **same basic recipe** — a "transformer" architecture filled with weights. The architecture is not the secret sauce; transformer designs are widely published and broadly similar.
- What separates a frontier chat model from this 90 MB embedding model comes down to **three things, all of which end up encoded in the weights**: (1) the *number* of weights — billions instead of ~22 million; (2) the staggering amount of data and computing power used to train them; and (3) extra training afterward (instruction-following, safety tuning) that further shapes those same weights.
- Take a top chat model, **delete its weights but keep its architecture, and you have the same empty shell that outputs nonsense** — identical in principle to the blank embedding model. The fluency, the knowledge, the apparent reasoning: all of it is the specific values of the weights. The blueprint is just plumbing.

Two practical differences explain this project's setup:

| | This project's embedding model | A chat model (the GPT / Claude family) |
|---|---|---|
| **Job** | Turn text into a "meaning fingerprint" (a vector) for search. Does **not** write text. | Generate text — write answers word by word. |
| **Size of weights** | ~22 million numbers, ~90 MB file | Hundreds of billions of numbers — hundreds of GB to over a terabyte |
| **Where the weights live** | **Open** — downloadable; stored on this server and run locally | **Closed** — not downloadable; they sit on the provider's servers and are reached over the internet through an API |

That last row is exactly why the pipeline does two different things. The **embedding** weights are small and open, so they are downloaded once and run on this server (the subject of this incident). The **text-generation** weights are huge and private, so the project sends the question to a provider's API and the model runs on *their* servers. Both are "transformer architecture + weights" — the difference is size, openness, and where the weights physically sit.

**The one-line takeaway:** an AI's capability is its weights. The architecture is a reusable, mostly-public skeleton; the weights are the expensive, trained-in "brain" — and whoever has the right weights has the working model.

---

## What Happened

While verifying the new agent feature, every run that touched document search printed:

```
Warning: You are sending unauthenticated requests to the HF Hub. Please set a
HF_TOKEN to enable higher rate limits and faster downloads.
Loading weights:   0%|          | 0/103 [00:00<?, ?it/s]
Loading weights: 100%|██████████| 103/103 [00:00<00:00, 2541.94it/s]
```

Two things to read here:

- **"Loading weights … 103"** — the software is pulling in the 90 MB weights file (split into 103 pieces) and loading it into memory.
- **"unauthenticated requests to the HF Hub"** — it contacted the Hugging Face website **without a login** to get (or check on) those weights.

The natural reaction is the one in the title of this doc: *I thought this ran locally — why is it on the internet?* It does run locally. But the weights themselves were never made a permanent part of the server, so the software went out and fetched them.

---

## Root Cause

The Docker image (the "snapshot" the server boots from) installed the **library** (`sentence-transformers`) but never downloaded the **weights**. So the weights lived only in a temporary cache folder created the first time something asked for them — and that folder sits on the container's **ephemeral** (throwaway) storage, which is wiped clean every time the pod restarts.

The chain of events on every restart:

1. Pod restarts → the temporary weights cache is gone.
2. A user (or a verification run) asks a question → the code calls `SentenceTransformer("all-MiniLM-L6-v2")`.
3. The library looks in the cache, finds nothing, and **downloads the 90 MB of weights from Hugging Face** — anonymously, because no login token was set.
4. It saves them back to the temporary cache and answers the question.
5. Next restart → back to step 1.

This is the same family of problem as the [2026-05-12 incident](2026-05-12-sentence-transformers-cuda-disk-wipe-mlvenv.md), where AI *packages* installed at runtime vanished on restart. Here it's the model *weights* doing the vanishing. The lesson rhymes: **anything that isn't baked into the image is temporary.**

### Why it mattered even though nothing broke

The download succeeded every time I tested it, so no question ever failed. But it meant a call to an outside website was sitting **inside the path of answering a user's question**. If Hugging Face were down, slow, or decided to throttle anonymous traffic (which the warning is literally hinting at — "set a token for higher limits"), then the first question after a restart would hang or fail, and document search would be dead until the download eventually succeeded. That's an avoidable outside dependency in a place it has no business being.

---

## Fix

Two changes in the Docker image build (`airflow/docker/Dockerfile`), applied together:

### Change 1: Download the weights once, at build time, and store them permanently in the image

```dockerfile
# HF_HOME pins a fixed folder inside the image for the model weights cache.
ENV HF_HOME=/opt/ml-venv/hf-cache
# Download the ~90 MB weights now, during the build, so they become a permanent part of the image.
RUN /opt/ml-venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
```

Because this runs during `docker build`, the weights become part of the image's permanent snapshot — exactly like baking in the software packages. Every pod that boots from the image already has the weights on disk. The name passed here (`all-MiniLM-L6-v2`) is identical to the one the running code uses, so at runtime it finds this baked copy instead of reaching for the internet.

### Change 2: Tell the software it is "offline" so it never contacts the website again

```dockerfile
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
```

Even with the weights already on disk, the library will, by default, still ping Hugging Face to check whether a newer version exists — which is what produced the warning. Setting these two switches tells it: "use only what's on disk; never contact the Hub." This removes the network call entirely **and** the warning along with it. (These come *after* the download step in the Dockerfile, so the build itself can still fetch the weights — only the finished image is offline.)

---

## Why This Fix

**Why bake the weights in instead of just setting a login token (`HF_TOKEN`)?**
A token would raise the anonymous rate limit, but the download would *still happen on every restart* and *still be in the request path*. That treats the symptom, not the cause. Baking the weights in removes the download completely. The model never needs the internet because it already has everything it needs locally — which was the original intent of running it "locally" in the first place.

**Why is this safe / why won't offline mode break loading?**
The weights are pre-downloaded into the exact folder (`HF_HOME`) the runtime reads from, under the exact model name the code requests. So when the code loads the model in offline mode, it's a guaranteed local hit. Offline mode only blocks the *network*; it does not block reading the file that's already there.

**What's the cost?**
The image is about 90 MB larger and the build takes a minute or two longer — both one-time, at build time, and well worth removing a live internet dependency from every question.

---

## Verification

After the next full deploy (which rebuilds the image with these steps), a document-search run should:

- **No longer print** the `unauthenticated requests to the HF Hub` warning.
- Load the model instantly from the baked-in copy.

A direct check that the weights are present in the image and load with the network switched off:

```bash
kubectl exec -n airflow-my-namespace airflow-scheduler-0 -c scheduler -- \
  /opt/ml-venv/bin/python -c "from sentence_transformers import SentenceTransformer; \
    m = SentenceTransformer('all-MiniLM-L6-v2'); print('loaded offline, dim =', m.get_sentence_embedding_dimension())"
# Expected: loaded offline, dim = 384   (and no HF Hub warning)
```

---

## Lessons / Notes

- **"Runs locally" and "downloads from the internet" can both be true at once.** Running the model (doing the math) is local. *Getting the weights* is a separate step that, by default, reaches out to a public website. They are easy to conflate; they are not the same thing.

- **A model is two parts: the structure and the weights.** The structure is small generic code that ships with the library. The weights are the big, model-specific file of learned numbers, hosted separately and fetched by name. The structure without the weights is a blank machine; the weights are what make it actually know anything.

- **If it isn't baked into the image, it's temporary.** This is now the second time a missing-after-restart problem traced back to something living only in the container's throwaway storage — first the AI packages, now the model weights. The rule of thumb: anything the app needs to run should be inside the Docker image, not fetched or installed at runtime.

- **A warning is a hint about a future failure, not just noise.** Nothing failed here. But the "unauthenticated requests… set a token for higher limits" warning was the system politely explaining exactly how it would eventually break (rate-limited anonymous downloads). Reading warnings as early failure reports is cheaper than waiting for the failure.

- **Where this could still bite later:** if a future feature runs the model from a *different* container (for example, a separate web app image), that image would need the same two changes — bake the weights in and set offline mode — or it would re-introduce the runtime download. The fix lives per-image, not globally.
