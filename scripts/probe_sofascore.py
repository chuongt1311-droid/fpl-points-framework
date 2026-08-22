# scripts/probe_sofascore.py — one-off probe, not part of the pipeline.
#
# Re-confirms the M4/Sofascore block documented in docs/PROJECT_LOG.md and
# docs/DECISION_RULE.md (2026-08-20/21 investigations): an edge ACL, most
# plausibly geo/ASN-based, returns an identical instant 403 from Sofascore's
# own origin on both sofascore.com and api.sofascore.com.
#
# Deliberately minimal: default UA, no retries, no evasion, no proxy. Per
# containment rule A3.3 ("no evasion... if it starts returning 403, it stops,
# it does not escalate") — if this 403s again, we stop and log it. We do not
# escalate to a headed browser, proxy, or alternate egress.
import json
import datetime
import urllib.request
import urllib.error

TARGETS = [
    "https://api.sofascore.com/api/v1/sport/football/events/live",
    "https://www.sofascore.com/robots.txt",
]

out = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "results": []}
for url in TARGETS:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            out["results"].append(
                {"url": url, "status": r.status, "bytes": len(r.read(2048))}
            )
    except urllib.error.HTTPError as e:
        out["results"].append(
            {
                "url": url,
                "status": e.code,
                "server": e.headers.get("Server"),
                "body": e.read(200).decode("utf-8", "replace"),
            }
        )
    except Exception as e:
        out["results"].append({"url": url, "error": f"{type(e).__name__}: {e}"})

print(json.dumps(out, indent=2))
