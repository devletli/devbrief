"""n8n akis guvenligi: statik yapi denetimi (canli n8n yok).

Kurallar: gecerli JSON; baglanti hedefleri mevcut; yapiSkan not disinda
yetim yok; yayin yollari Approve + publish_allowed(true) uzerinden;
Approve yalnizca onay-IF dalindan; sabit public yok; webhook auth var;
sir yok; onay mesaji detay ciktisina bakar.
"""

import json
from pathlib import Path

N8N = Path(__file__).parent.parent / "n8n"
STICKY = "n8n-nodes-base.stickyNote"
TELEGRAM_TOKEN = __import__("re").compile(r"\d{8,10}:[A-Za-z0-9_-]{35}")


def _flows():
    return {f.name: json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(N8N.glob("*.json"))}


def _edges(flow):
    """(kaynak, cikis_indeksi, hedef) listesi."""
    out = []
    for src, conns in flow.get("connections", {}).items():
        for idx, branch in enumerate(conns.get("main", [])):
            for v in branch:
                out.append((src, idx, v["node"]))
    return out


def _paths(flow, target):
    """Trigger'lardan hedefe tum basit yollar (kucuk cizge, DFS)."""
    adj: dict[str, list[str]] = {}
    for src, _, dst in _edges(flow):
        adj.setdefault(src, []).append(dst)
    starts = [n["name"] for n in flow["nodes"]
              if n["type"].endswith(("Trigger", "trigger"))
              or n["type"] == "n8n-nodes-base.webhook"]
    found = []

    def _dfs(node, seen):
        if node == target:
            found.append(list(seen) + [node])
            return
        for nxt in adj.get(node, []):
            if nxt not in seen:
                _dfs(nxt, seen + [node])

    for s in starts:
        _dfs(s, [])
    return found


def test_all_flows_valid_and_linked():
    flows = _flows()
    assert "02-approve-and-publish.json" in flows
    for name, flow in flows.items():
        names = [n["name"] for n in flow["nodes"]]
        assert len(names) == len(set(names)), f"{name}: yinelenen ad"
        for src, _, dst in _edges(flow):
            assert src in names and dst in names, f"{name}: kirik bag {src}->{dst}"
        connected = set()
        for src, _, dst in _edges(flow):
            connected.add(src)
            connected.add(dst)
        orphans = [n["name"] for n in flow["nodes"]
                   if n["name"] not in connected and n["type"] != STICKY]
        assert not orphans, f"{name}: yetim {orphans}"
        blob = json.dumps(flow)
        assert "sk-or-" not in blob, f"{name}: sir"
        assert not TELEGRAM_TOKEN.search(blob), f"{name}: telegram token"


def test_publish_paths_guarded():
    flow = _flows()["02-approve-and-publish.json"]
    by_name = {n["name"]: n for n in flow["nodes"]}
    publishers = [n for n in by_name
                  if n in ("YouTube Yükle",) or "Yayın Merkezi" in n or n == "POST Published"]
    assert publishers, "yayin dugumu yok"
    for target in publishers:
        paths = _paths(flow, target)
        assert paths, f"{target}: erisilemez"
        for p in paths:
            assert "POST Approve" in p, f"{target}: Approve atlanmis: {p}"
            assert "Yayın İzni?" in p, f"{target}: izin IF yok: {p}"
    # Approve yalnizca onay IF'inin dogru dalindan.
    incoming = [(s, i) for s, i, d in _edges(flow) if d == "POST Approve"]
    assert incoming and all(s == "Onaylandı mı?" and i == 0 for s, i in incoming), incoming
    # Yayın İzni true dalinda yayin, false dalinda shadow mesaji.
    izin_out = [e for e in _edges(flow) if e[0] == "Yayın İzni?"]
    true_targets = {d for s, i, d in izin_out if i == 0}
    false_targets = {d for s, i, d in izin_out if i == 1}
    assert "Telegram Shadow" in false_targets
    assert {"Video Indir (YouTube)",
            "Yayın Merkezi (X, LinkedIn, IG, TikTok, Threads...)"} <= true_targets


def test_no_hardcoded_public_and_webhook_auth():
    flow = _flows()["02-approve-and-publish.json"]
    blob = json.dumps(flow)
    assert '"privacyStatus": "public"' not in blob.replace(" ", "").replace("\\", "")
    assert '"privacyStatus":"private"' in blob.replace(" ", "").replace("\\", "")
    webhooks = [n for n in flow["nodes"] if n["type"] == "n8n-nodes-base.webhook"]
    assert webhooks and all("httpHeaderAuth" in json.dumps(n.get("credentials", {}))
                            for n in webhooks)


def test_approval_message_uses_detail():
    flow = _flows()["02-approve-and-publish.json"]
    by_name = {n["name"]: n for n in flow["nodes"]}
    msg = json.dumps(by_name["Onay Bekle"])
    assert "GET Episode" in msg
    assert "GET Package" not in msg
    for field in ("metadata.x_post", "injection_suspected", "low_source",
                  "publish_mode", "MOD?"):
        assert field in msg, field
    # Package yalnizca Approve sonrasi.
    pkg_in = [s for s, _, d in _edges(flow) if d == "GET Package"]
    assert pkg_in == ["POST Approve"], pkg_in


def test_broken_flow_fails_guards():
    import copy

    import pytest

    flow = _flows()["02-approve-and-publish.json"]
    broken = copy.deepcopy(flow)
    # Saldiri: onay IF'i Approve'u atlayip dogrudan GET Package'e baglanmis.
    for branch in broken["connections"]["Onaylandı mı?"]["main"]:
        for v in branch:
            if v["node"] == "POST Approve":
                v["node"] = "GET Package"
    with pytest.raises(AssertionError):
        for p in _paths(broken, "YouTube Yükle"):
            assert "POST Approve" in p, f"Approve'suz yol: {p}"
