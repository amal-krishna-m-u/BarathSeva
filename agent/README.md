# BarathSeva Civic Triage — aiKart "Try Me Now" agent

Submission artifacts for the aiKart sandbox (manifest `apiVersion: aikart.dev/v1`).
Team: **startrek**.

| File | Purpose |
| --- | --- |
| `aikart-manifest.yaml` | The manifest uploaded in the "Try / Run My Agent" step |
| `Dockerfile` | Builds the sandbox image (build from the **repo root**) |
| `run.py` | Implements the aiKart runtime contract |
| `samples.py` | Synthetic demo photographs, generated at runtime |

## What the agent does

Given a citizen's complaint text and a photograph, it runs the triage decision:
file forensics, an **image-vs-text check**, verification, category and priority,
the owning department, and the SLA deadline. It renders the result as markdown.

## Why it is the triage agent and not the whole platform

BarathSeva is FastAPI + PostGIS + Redis + Celery + a Next.js front end. The
sandbox is one ephemeral container with no network by default, no persistence
and a wall-clock cap, so a web platform cannot run there. The triage decision
can, because every reference table it needs — wards, departments, SLA hours,
the category vocabulary — is plain Python data in `app/core/city.py`.

The database-backed authenticity layers (perceptual-hash reuse across prior
complaints, reporter velocity, corroboration) genuinely cannot run here. The
output says so rather than printing a confident verdict over missing controls.

## Two constraints the manifest forced

**No file upload.** aiKart v1 input types are `text | textarea | number |
boolean | select`. A photo therefore arrives as one of the bundled synthetic
samples or as base64 pasted into a textarea. The samples are drawn from
primitives and depict nobody — publishing a real person's likeness in a public
image to demo a "this is not a pothole" check is not a trade worth making.

**No secret injection.** The manifest has no mechanism for credentials and the
image is public, so no API key is baked in. With no key the agent runs fully
offline on deterministic rules and makes no outbound request; a buyer who wants
the real vision check supplies their own Gemini key as an input.

## Build, test, publish

```bash
# From the repository root — the agent imports backend/app
docker build -f agent/Dockerfile -t startrek/barathseva-civic-triage:1.0.0 .

# Run it exactly as aiKart does: mounted /aikart, no network, capped resources
mkdir -p /tmp/aikart && cat > /tmp/aikart/input.json <<'JSON'
{"description":"Large pothole on 5th Main, Koramangala. Dangerous for two-wheelers.",
 "sample_photo":"Road with a pothole","locality":"Koramangala"}
JSON
docker run --rm --network none --cpus 1 --memory 2048m \
  -v /tmp/aikart:/aikart startrek/barathseva-civic-triage:1.0.0
cat /tmp/aikart/output.json

# Publish to a namespace your team owns, then submit the manifest
docker push startrek/barathseva-civic-triage:1.0.0
```

**Before submitting:** `runtime.image` in the manifest must point at a namespace
you control and be anonymously pullable — aiKart cannot use private registries.

## Manifest limits observed

| Field | Requested | Cap |
| --- | --- | --- |
| `resources.cpu` | 1 | 2 |
| `resources.memoryMb` | 2048 | 4096 |
| `resources.timeoutSeconds` | 240 | 280 |

`security.networkEgress` is `allowlist` with a single domain,
`generativelanguage.googleapis.com`, used only when a buyer supplies a key.
Set it to `none` if you would rather the listing never reach the network — the
agent degrades to deterministic rules and still produces a full report.
