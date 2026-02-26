#!/usr/bin/env python3
"""
Toronto Hydro Power Outage Status Fetcher
=========================================
Fetches raw outage data from the Toronto Hydro outage map, which is powered
by Kubra StormCenter (https://kubra.io).

Kubra StormCenter exposes a tiered JSON API:
  1. currentState  – deployment metadata and path to the CDN data bucket
  2. data.json     – top-level summary of all active outage clusters
  3. {quadkey}.json – individual outage records per map tile (quadkey-addressed)

Real currentState response shape (Toronto Hydro, confirmed 2026-02-25):
  {
    "stormcenterDeploymentId": "<uuid>",
    "data": {
      "interval_generation_data":         "data/<uuid>",
      "cluster_interval_generation_data": "cluster-data/{qkh}/<uuid>/<uuid>",
      "planned_outage_data": null,
      "additional_map_layer_data": {}
    },
    "datastatic": { "<instance-uuid>": "regions/<uuid>" },
    ...
  }

The {qkh} token in cluster_interval_generation_data is replaced at runtime with
the first few characters of a quadkey tile string (a CDN sharding prefix).

Usage:
    python get_outage_status.py                  # pretty-print summary JSON
    python get_outage_status.py --raw            # all outage records as JSON array
    python get_outage_status.py --summary        # human-readable summary
    python get_outage_status.py --state-only     # dump raw currentState JSON
    python get_outage_status.py --discover       # auto-discover IDs from the live page

Requirements:
    pip install requests
"""

import argparse
import json
import re
import sys
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Known Toronto Hydro / Kubra identifiers
# These can also be discovered at runtime via --discover.
# ---------------------------------------------------------------------------
TORONTO_HYDRO_OUTAGE_PAGE = "https://outagemap.torontohydro.com"
KUBRA_BASE = "https://kubra.io/"
KUBRA_STORMCENTER_BASE = f"{KUBRA_BASE}stormcenter/"

# Toronto Hydro's Kubra view ID (from the embedded iframe URL).
# Full view URL: https://kubra.io/stormcenter/views/b7626c3d-feea-40d6-ae65-944aa67ffeea
KNOWN_VIEW_ID = "b7626c3d-feea-40d6-ae65-944aa67ffeea"

# The stormcenter (instance) ID is paired with the view ID and appears in the
# currentState URL: /stormcenter/api/v1/stormcenters/{INSTANCE_ID}/views/{VIEW_ID}/currentState
#
# It can also be found as the key in the `datastatic` map in the currentState
# response body (e.g. "datastatic": { "<INSTANCE_ID>": "regions/..." }).
#
# To confirm: open https://outagemap.torontohydro.com in a browser, open
# DevTools → Network tab, filter for "currentState", and copy the first UUID
# from the request URL.  Then set this constant or pass --instance-id <ID>.
KNOWN_INSTANCE_ID: Optional[str] = "c3ecf8d4-47fb-4846-9070-70cb83d5368d"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TorontoPowerOutageBot/1.0; "
        "+https://github.com/sandeep406/GetTorontoPowerOutageStatus)"
    ),
    "Accept": "application/json",
}


# ---------------------------------------------------------------------------
# ID discovery
# ---------------------------------------------------------------------------

def discover_ids(verbose: bool = False) -> tuple[Optional[str], Optional[str]]:
    """
    Attempt to auto-discover the Kubra stormcenter instance ID and view ID
    by fetching the Toronto Hydro outage page and scanning for Kubra API URLs.

    Returns (instance_id, view_id). Either may be None if not found.
    """
    instance_id: Optional[str] = None
    view_id: Optional[str] = None

    uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    kubra_api_re = re.compile(
        rf"stormcenters/({uuid_pattern})/views/({uuid_pattern})/currentState",
        re.IGNORECASE,
    )
    kubra_view_re = re.compile(
        rf"stormcenter/views/({uuid_pattern})",
        re.IGNORECASE,
    )

    urls_to_try = [
        TORONTO_HYDRO_OUTAGE_PAGE,
        "https://www.torontohydro.com/for-home/outage-centre/outage-map",
        "https://www.torontohydro.com/outage-map",
    ]

    for url in urls_to_try:
        try:
            if verbose:
                print(f"[discover] Fetching {url} ...", file=sys.stderr)
            resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
            text = resp.text

            # Look for the full API URL pattern first (most reliable)
            m = kubra_api_re.search(text)
            if m:
                instance_id, view_id = m.group(1), m.group(2)
                if verbose:
                    print(f"[discover] Found via API URL: instance={instance_id} view={view_id}", file=sys.stderr)
                return instance_id, view_id

            # Fallback: look for the view URL pattern
            m = kubra_view_re.search(text)
            if m:
                view_id = m.group(1)
                if verbose:
                    print(f"[discover] Found view ID: {view_id}", file=sys.stderr)
                # instance_id still unknown; will be None
        except requests.RequestException as e:
            if verbose:
                print(f"[discover] Failed to fetch {url}: {e}", file=sys.stderr)
            continue

    return instance_id, view_id


# ---------------------------------------------------------------------------
# Kubra API helpers
# ---------------------------------------------------------------------------

def get_current_state(instance_id: str, view_id: str) -> dict:
    """
    Fetch the currentState endpoint. Returns the full JSON response.

    Key fields in the Toronto Hydro response:
      - stormcenterDeploymentId          – identifies the active deployment
      - data.interval_generation_data    – CDN path prefix for summary/tile data
      - data.cluster_interval_generation_data – cluster tile path (contains {qkh})
      - datastatic                       – map of instance_id → regions path
    """
    url = (
        f"{KUBRA_STORMCENTER_BASE}api/v1/stormcenters/{instance_id}"
        f"/views/{view_id}/currentState?preview=false"
    )
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_configuration(instance_id: str, view_id: str, deployment_id: str) -> dict:
    """Fetch the configuration endpoint for layer/cluster settings."""
    url = (
        f"{KUBRA_STORMCENTER_BASE}api/v1/stormcenters/{instance_id}"
        f"/views/{view_id}/configuration/{deployment_id}?preview=false"
    )
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_summary_data(data_path: str) -> dict:
    """
    Fetch the top-level summary JSON from the CDN data bucket.

    data_path comes from state["data"]["interval_generation_data"], e.g.
    "data/86a5d347-964f-4d94-873d-6f235d079b72/" (trailing slash added by caller).

    Full URL example:
      https://kubra.io/data/86a5d347-964f-4d94-873d-6f235d079b72/public/summary-1/data.json
    """
    url = f"{KUBRA_BASE}{data_path}public/summary-1/data.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_tile_data(data_path: str, layer_name: str, quadkey: str) -> dict:
    """
    Fetch outage data for a single map tile (identified by quadkey).

    Full URL example:
      https://kubra.io/data/<uuid>/public/outage/<quadkey>.json
    """
    url = f"{KUBRA_BASE}{data_path}public/{layer_name}/{quadkey}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json()


def collect_all_outages(data_path: str, layer_name: str, summary: dict) -> list[dict]:
    """
    Walk the summary tree and collect every individual outage record.

    The summary is a nested tile structure. Leaf nodes contain outage arrays;
    cluster nodes have a 'file' reference pointing to a sub-tile JSON.
    """
    outages: list[dict] = []

    def walk(node: dict, depth: int = 0) -> None:
        # Leaf outage records
        if "desc" in node and "id" in node:
            outages.append(node)
            return
        # Sub-tile cluster reference
        if "file" in node:
            sub = get_tile_data(data_path, layer_name, node["file"])
            if sub:
                walk(sub, depth + 1)
            return
        # Container node with nested tiles
        for value in node.values():
            if isinstance(value, dict):
                walk(value, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        walk(item, depth + 1)

    walk(summary)
    return outages


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Toronto Hydro power outage data from the Kubra StormCenter API."
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Output all outage records as a raw JSON array.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a human-readable summary instead of full JSON.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Auto-discover Kubra IDs from the live Toronto Hydro outage page.",
    )
    parser.add_argument(
        "--instance-id",
        default=None,
        help="Override the Kubra stormcenter instance ID.",
    )
    parser.add_argument(
        "--view-id",
        default=KNOWN_VIEW_ID,
        help=f"Override the Kubra view ID (default: {KNOWN_VIEW_ID}).",
    )
    parser.add_argument(
        "--state-only",
        action="store_true",
        help="Only fetch and print the currentState JSON (useful for debugging IDs).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print diagnostic messages to stderr.",
    )
    args = parser.parse_args()

    instance_id = args.instance_id or KNOWN_INSTANCE_ID
    view_id = args.view_id

    # ---- Step 1: Resolve IDs -----------------------------------------------
    if args.discover or instance_id is None:
        if args.verbose:
            print("[info] Running ID discovery ...", file=sys.stderr)
        discovered_instance, discovered_view = discover_ids(verbose=args.verbose)
        if discovered_instance:
            instance_id = discovered_instance
        if discovered_view and view_id == KNOWN_VIEW_ID:
            view_id = discovered_view
        if args.discover:
            print(json.dumps({"instance_id": instance_id, "view_id": view_id}, indent=2))
            return

    if instance_id is None:
        print(
            "ERROR: Could not determine the Kubra stormcenter instance ID.\n\n"
            "To find it:\n"
            "  1. Visit https://outagemap.torontohydro.com in a browser\n"
            "  2. Open DevTools → Network tab → filter for 'currentState'\n"
            "  3. Copy the first UUID from the URL:\n"
            "     .../stormcenters/{INSTANCE_ID}/views/{VIEW_ID}/currentState\n"
            "  4. Re-run with: --instance-id <INSTANCE_ID>\n\n"
            "Alternatively, run with --discover to attempt auto-detection.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.verbose:
        print(f"[info] instance_id={instance_id}", file=sys.stderr)
        print(f"[info] view_id={view_id}", file=sys.stderr)

    # ---- Step 2: currentState ----------------------------------------------
    try:
        state = get_current_state(instance_id, view_id)
    except requests.HTTPError as e:
        print(f"ERROR fetching currentState: {e}", file=sys.stderr)
        sys.exit(1)

    if args.state_only:
        print(json.dumps(state, indent=2))
        return

    if args.verbose:
        print(f"[info] currentState fetched OK", file=sys.stderr)

    # Extract data path and deployment info from state.
    # Toronto Hydro uses: state["data"]["interval_generation_data"] = "data/<uuid>"
    # Fallback keys cover other Kubra deployments.
    state_data: dict = state.get("data") or {}
    data_path: Optional[str] = (
        state_data.get("interval_generation_data")
        or state.get("dataPath")
        or state.get("data_path")
    )
    # Ensure the path ends with "/" so URL construction is consistent
    if data_path and not data_path.endswith("/"):
        data_path = data_path + "/"

    deployment_id: Optional[str] = (
        state.get("stormcenterDeploymentId")
        or state.get("deploymentId")
        or state.get("deployment_id")
    )

    if args.verbose:
        print(f"[info] deploymentId={deployment_id}", file=sys.stderr)
        print(f"[info] dataPath={data_path}", file=sys.stderr)

    # ---- Step 3: Summary data ----------------------------------------------
    if data_path is None:
        # No data path means no active outages or the API structure differs;
        # return the raw state for inspection.
        print(json.dumps(state, indent=2))
        return

    try:
        summary = get_summary_data(data_path)
    except requests.HTTPError as e:
        print(f"ERROR fetching summary data: {e}", file=sys.stderr)
        print("Raw currentState:", file=sys.stderr)
        print(json.dumps(state, indent=2), file=sys.stderr)
        sys.exit(1)

    # ---- Step 4: Collect individual outages (for --raw) --------------------
    if args.raw:
        # Determine the cluster layer name from the state config
        layer_name = "cluster"  # default fallback
        for layer in state.get("layerConfigs", []):
            if layer.get("type") in ("cluster", "outage"):
                layer_name = layer.get("name", layer_name)
                break

        outages = collect_all_outages(data_path, layer_name, summary)
        print(json.dumps(outages, indent=2))
        return

    # ---- Step 5: Human-readable summary ------------------------------------
    if args.summary:
        totals = summary.get("summaryFileData", summary.get("totals", {}))
        customers_affected = totals.get("total_cust_a", totals.get("customersAffected", "?"))
        outage_count = totals.get("total_outages", totals.get("outageCount", "?"))
        print(f"Toronto Hydro Power Outage Status")
        print(f"==================================")
        print(f"Customers affected : {customers_affected}")
        print(f"Active outages     : {outage_count}")
        print(f"Deployment ID      : {deployment_id}")
        print()
        print("Full summary JSON:")
        print(json.dumps(summary, indent=2))
        return

    # Default: print the full summary JSON
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
