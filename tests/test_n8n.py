"""n8n akislari: gecerli JSON, kirik baglanti yok, API ile uyumlu (canli n8n yok)."""

import json
import re
from pathlib import Path

N8N = Path(__file__).parent.parent / "n8n"

API_PATHS = {"/run", "/jobs", "/episodes", "/health", "/package", "/approve",
             "/reject", "/published", "/metrics", "/video", "/thumb"}
EPISODE_LIST_FIELDS = {"id", "slug", "title", "source_url", "claim_ozeti", "claim_sayisi",
                       "yorum_sayisi", "injection_suspected", "low_source"}


def _flows():
    out = {}
    for f in sorted(N8N.glob("*.json")):
        out[f.name] = json.loads(f.read_text(encoding="utf-8"))
    assert out, "n8n/*.json yok"
    return out


def test_all_flows_valid():
    for name, flow in _flows().items():
        assert flow.get("nodes"), name
        names = [n["name"] for n in flow["nodes"]]
        assert len(names) == len(set(names)), f"{name}: yinelenen dugum adi"
        for src, conns in flow.get("connections", {}).items():
            assert src in names, f"{name}: {src} tanimsiz"
            for out in conns.get("main", []):
                for v in out:
                    assert v["node"] in names, f"{name}: {v['node']} tanimsiz"
        blob = json.dumps(flow)
        assert "sk-or-" not in blob and "Bearer " not in blob, f"{name}: sir sizmis"


def _urls(flow: dict) -> list[str]:
    out = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "url" and isinstance(v, str):
                    out.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(flow)
    return out


def test_flow_02_matches_api():
    flow = _flows()["02-approve-and-publish.json"]
    blob = json.dumps(flow)
    for frag in ("splitOut", "partial_claims"):
        assert frag not in blob, f"eski alan kalmis: {frag}"
    for u in _urls(flow):
        if "hub_url" in u:
            continue  # dis yayin merkezi, bizim API degil
        assert any(p in u for p in ("/run", "/episodes", "/jobs", "/health")), u
    refs = set(re.findall(r"Bölümlere Ayır'\)\.item\.json\.(\w+)", blob))
    assert refs <= EPISODE_LIST_FIELDS | {"video_url", "platforms"}, refs - EPISODE_LIST_FIELDS


def test_schedules_present():
    flows = _flows()
    crons = {}
    for name, flow in flows.items():
        for n in flow["nodes"]:
            if n["type"] == "n8n-nodes-base.scheduleTrigger":
                for rule in n["parameters"]["rule"]["interval"]:
                    crons.setdefault(name, []).append(rule.get("expression"))
    assert crons.get("01-daily-trigger.json") == ["0 7 * * 1-5"]
    assert "03-error-handler.json" in flows
    assert any("errorTrigger" in n["type"] for n in flows["03-error-handler.json"]["nodes"])
