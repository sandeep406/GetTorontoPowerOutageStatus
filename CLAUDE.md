# CLAUDE.md — GetTorontoPowerOutageStatus

This file describes the repository for AI assistants (Claude Code and similar tools).

---

## Overview

**GetTorontoPowerOutageStatus** is a Python utility that fetches live power outage data
from the [Toronto Hydro outage map](https://outagemap.torontohydro.com), which is powered
by the [Kubra StormCenter](https://kubra.io) platform.

The script queries the Kubra JSON API directly to retrieve raw outage records without
needing a browser or scraping the rendered page.

---

## Repository Structure

```
GetTorontoPowerOutageStatus/
├── get_outage_status.py   # Main script — fetches outage data from Kubra API
├── CLAUDE.md              # This file
└── README.md              # (to be added)
```

---

## Key File: `get_outage_status.py`

### Purpose
Fetches Toronto Hydro power outage data from the Kubra StormCenter JSON API.

### How the Kubra API Works

Kubra StormCenter exposes a tiered API at `https://kubra.io/`:

| Step | Endpoint | Purpose |
|------|----------|---------|
| 1 | `stormcenter/api/v1/stormcenters/{INSTANCE_ID}/views/{VIEW_ID}/currentState?preview=false` | Returns deployment metadata and CDN data paths |
| 2 | `{data_path}public/summary-1/data.json` | Top-level cluster/tile summary of all active outages |
| 3 | `{data_path}public/{layer_name}/{quadkey}.json` | Individual outage records per map tile (quadkey-addressed) |

### Confirmed `currentState` Response Shape (Toronto Hydro, 2026-02-25)

```json
{
  "version": "V1",
  "stormcenterDeploymentId": "a8dd5a5d-3ab9-424c-8fb4-fc9f7da31a42",
  "messageBundleId": "d4pSsu",
  "updatedAt": 1772066437000,
  "data": {
    "interval_generation_data":         "data/86a5d347-964f-4d94-873d-6f235d079b72",
    "cluster_interval_generation_data": "cluster-data/{qkh}/092d35db-2790-4944-9b2d-b22397ed3f70/86a5d347-964f-4d94-873d-6f235d079b72",
    "planned_outage_data": null,
    "additional_map_layer_data": {}
  },
  "datastatic": {
    "c3ecf8d4-47fb-4846-9070-70cb83d5368d": "regions/05b4d859-2b1a-45d7-ac69-9b5fcfe8013c"
  },
  "controlCenter": { ... }
}
```

**Key fields:**
- `data.interval_generation_data` → append `/public/summary-1/data.json` to get the outage summary
- `data.cluster_interval_generation_data` → `{qkh}` is replaced with the first few chars of a quadkey (CDN sharding prefix)
- `datastatic` key → the key UUID is the `INSTANCE_ID`

### Known Toronto Hydro Identifiers

| Identifier | Value | Notes |
|------------|-------|-------|
| `VIEW_ID` | `b7626c3d-feea-40d6-ae65-944aa67ffeea` | From `https://kubra.io/stormcenter/views/{VIEW_ID}` |
| `INSTANCE_ID` | `c3ecf8d4-47fb-4846-9070-70cb83d5368d` | First UUID in the `currentState` request URL |

**Finding the `INSTANCE_ID`:**
- It appears as the **key** in the `datastatic` field of the currentState response
- Or inspect the full request URL: `.../stormcenters/{INSTANCE_ID}/views/{VIEW_ID}/currentState`
  - In a browser: DevTools → Network → filter `currentState`
  - On mobile: capture traffic with Proxyman and look for requests to `kubra.io`

Once confirmed, set `KNOWN_INSTANCE_ID` at the top of `get_outage_status.py`.

### CLI Usage

```bash
# Install dependencies
pip install requests

# Auto-discover Kubra IDs from the live page (outputs JSON with instance_id + view_id)
python get_outage_status.py --discover

# Fetch the currentState only (useful for debugging / confirming IDs)
python get_outage_status.py --instance-id <ID> --state-only

# Pretty-print the top-level summary JSON
python get_outage_status.py --instance-id <ID>

# All outage records as a raw JSON array
python get_outage_status.py --instance-id <ID> --raw

# Human-readable summary (customers affected, outage count)
python get_outage_status.py --instance-id <ID> --summary

# Verbose diagnostic output to stderr
python get_outage_status.py --instance-id <ID> --verbose
```

### Dependencies

| Package | Purpose |
|---------|---------|
| `requests` | HTTP client for all Kubra API calls |

No other third-party dependencies. Python 3.10+ required (uses `tuple[...]` type hints).

---

## Development Conventions

### Branch Naming
Feature branches follow the pattern: `claude/<description>-<session-id>`

### Commit Style
- Short imperative subject line (≤72 chars)
- Body explains *why*, not just *what*

### Python Style
- Standard library first, then third-party imports
- Type hints on all function signatures
- Constants at module top, in `SCREAMING_SNAKE_CASE`
- `Optional[str]` for values that may not yet be known (e.g., `KNOWN_INSTANCE_ID`)

### No Framework / No Build Step
This is a single-file utility script. Do not add Flask, FastAPI, or similar unless
the project scope explicitly expands to an API server.

---

## Data Notes

- The outage map updates approximately every **10 minutes**
- Kubra uses **quadkey** tile addressing (Bing Maps quadkey scheme) at zoom levels 7–14
- Cluster tiles aggregate outages; leaf tiles contain individual incident records
- Each outage record includes: incident ID, estimated restoration time, confidence level,
  customers affected, crew status, start timestamp, and geographic coordinates

---

## Useful References

- [Kubra StormCenter product page](https://kubra.io)
- [Toronto Hydro outage map](https://outagemap.torontohydro.com)
- [fgregg/kubra — open-source Kubra scraper (PyPI)](https://github.com/fgregg/kubra)
- [openkentuckiana/power-outage-data — reference scraper implementation](https://github.com/openkentuckiana/power-outage-data)
