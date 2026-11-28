"""Capture a lutonada stage and its separate selection data. Not a simulator."""
import argparse
import json
import re
import sys
from urllib.parse import parse_qs, quote, urljoin, urlparse

from source_io import Snapshot, unwrap, write_json

SITE = "http://lutonada.online/"


def map_id(value):
    if "://" not in value:
        return value.strip()
    url = urlparse(value)
    if url.hostname != "lutonada.online":
        raise ValueError("Expected a lutonada.online URL or a stage ID/name")
    ids = parse_qs(url.query).get("id", [])
    if len(ids) != 1 or not ids[0]:
        raise ValueError("Map URL must contain exactly one nonempty id")
    return ids[0]


def discover(snapshot):
    page = snapshot.fetch(SITE, "site.html", as_json=False)
    scripts = re.findall(r'<script\b[^>]*\bsrc=["\x27]([^"\x27]+)["\x27]', page)
    if not scripts:
        raise ValueError("No script entry found; inspect current frontend")
    entry_url = urljoin(SITE, scripts[0])
    entry = snapshot.fetch(entry_url, "entry.js", as_json=False)
    match = re.search(r'BASE_URL\s*:\s*["\x27](https?://[^"\x27]+)["\x27]', entry)
    if not match:
        chunks = re.findall(r'["\x27]([^"\x27]*Home-[^"\x27/]+\.js)["\x27]', entry)
        if not chunks:
            raise ValueError("Home chunk not found; inspect current frontend")
        chunk = chunks[0]
        home_url = urljoin(SITE if chunk.startswith("assets/") else entry_url, chunk)
        home = snapshot.fetch(home_url, "home.js", as_json=False)
        match = re.search(r'BASE_URL\s*:\s*["\x27](https?://[^"\x27]+)["\x27]', home)
        if not match or "/enemy/data" not in home or "/recalRune/getData" not in home:
            raise ValueError("Frontend API contract changed; inspect it before adapting")
    return match[1].rstrip("/") + "/"


def leaves(value, parents=()):
    if isinstance(value, list):
        for child in value:
            yield from leaves(child, parents)
    elif isinstance(value, dict):
        if value.get("levelId") and value.get("id"):
            yield value, parents
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                label = value.get("type") or value.get("episode") or value.get("operation")
                lineage = parents + (str(label),) if key == "childNodes" and label else parents
                yield from leaves(child, lineage)


def select(index, target):
    entries = list(leaves(index))
    exact = [(node, group) for node, group in entries if node["id"] == target]
    if len(exact) == 1:
        return exact[0]
    matches = exact or [(node, group) for node, group in entries
                        if target.casefold() in {str(node.get(k, "")).casefold()
                                                 for k in ("id", "operation", "name")}]
    if len(matches) != 1:
        choices = [{"id": n["id"], "operation": n.get("operation"), "groups": g}
                   for n, g in matches[:30]]
        raise ValueError("Map must resolve uniquely; candidates=" + json.dumps(choices, ensure_ascii=False))
    return matches[0]


def query_refs(refs):
    # Match the site's base-prefab lookup; preserve original refs separately.
    selected = {}
    for ref in refs:
        prefab = (ref.get("overwrittenData") or {}).get("prefabKey") or {}
        base = ref
        if prefab.get("m_defined"):
            base = next((r for r in refs if r["id"] == prefab.get("m_value")), ref)
        selected.setdefault(base["id"], base)
    return list(selected.values())


def rune_selection(data, chosen, complete):
    runes = data.get("parsedRunes", [])
    by_id = {r["runeId"]: r for r in runes}
    unknown = set(chosen) - set(by_id)
    if unknown:
        raise ValueError("Unknown rune IDs: " + ", ".join(sorted(unknown)))
    selected = set(chosen)
    selected.update(r["runeId"] for r in runes if r.get("essential") and not r.get("exclusiveGroupId"))
    groups = {}
    for r in runes:
        if r.get("exclusiveGroupId"):
            groups.setdefault(r["exclusiveGroupId"], []).append(r)
    missing = []
    for name, options in groups.items():
        picked = selected & {r["runeId"] for r in options}
        if len(picked) > 1:
            raise ValueError("Mutually exclusive runes selected in group " + name)
        if any(r.get("essential") for r in options) and not picked:
            missing.append({"group": name, "options": [r["runeId"] for r in options]})
    return {"selected_ids": [r["runeId"] for r in runes if r["runeId"] in selected],
            "selection_declared_complete": complete, "missing_required_groups": missing,
            "status": "specified_not_applied" if complete and not missing else "needs_map_choices",
            "choices": [{k: r.get(k) for k in ("runeId", "essential", "exclusiveGroupId", "points", "description")}
                        for r in runes]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rune", action="append", default=[])
    parser.add_argument("--selection-complete", action="store_true")
    args = parser.parse_args()
    snapshot = None
    try:
        target = map_id(args.map)
        snapshot = Snapshot(args.out, "lutonada_map")
        backend = discover(snapshot)
        index = unwrap(snapshot.fetch(urljoin(backend, "json/storyCN.json"), "index.raw.json"))
        node, groups = select(index, target)
        level_id = node["levelId"]
        if any(p in ("", ".", "..") for p in level_id.split("/")):
            raise ValueError("Unexpected levelId path")
        level = unwrap(snapshot.fetch(urljoin(backend, "levels/" + quote(level_id, safe="/") + ".json"), "level.raw.json"))
        for key in ("mapData", "routes", "waves", "enemyDbRefs"):
            if key not in level:
                raise ValueError("Missing expected level field " + key)
        refs = query_refs(level["enemyDbRefs"])
        enemies = unwrap(snapshot.fetch(urljoin(backend, "enemy/data"), "enemies.response.json",
                                        {"language": "CN", "enemyRefs": refs}))
        if not isinstance(enemies, dict) or not isinstance(enemies.get("EnemyDatas"), list):
            raise ValueError("Enemy response has unexpected schema")
        found = {e.get("key") for e in enemies["EnemyDatas"]}
        missing_enemies = [r["id"] for r in refs if r["id"] not in found]
        if missing_enemies:
            raise ValueError("Enemy response incomplete: " + ", ".join(missing_enemies))
        matrix = "obt/recalrune/" in level_id
        selection = {"status": "no_matrix_choices", "selected_ids": []}
        if matrix:
            data = unwrap(snapshot.fetch(urljoin(backend, "recalRune/getData"), "matrix.response.json",
                                         {"levelId": level_id.rsplit("/", 1)[-1], "language": "CN"}))
            if not isinstance(data, dict) or not data.get("parsedRunes"):
                raise ValueError("Matrix stage is missing its separate rune data")
            selection = rune_selection(data, args.rune, args.selection_complete)
        elif args.rune or args.selection_complete:
            raise ValueError("--rune/--selection-complete currently support matrix stages only")
        contracts = unwrap(snapshot.fetch(urljoin(backend, "ccb/getData"), "contracts.response.json", {"mapId": node["id"]}))
        grid = level["mapData"].get("map", [])
        summary = {"requested_map": args.map, "site_url": SITE + "?id=" + quote(node["id"]),
                   "stage": node, "groups": groups, "level_id": level_id,
                   "source_version": "not_provided_by_site", "raw_options": level.get("options"),
                   "rows": len(grid), "columns": len(grid[0]) if grid else 0,
                   "route_entries": len(level["routes"]), "extra_route_entries": len(level.get("extraRoutes") or []),
                   "wave_entries": len(level["waves"]), "enemy_ref_entries": len(level["enemyDbRefs"]),
                   "enemy_response_entries": len(enemies["EnemyDatas"]), "matrix_selection": selection,
                   "other_selection_data_present": bool(contracts or level.get("optionalRunes") or level.get("sandTable")),
                   "input_url_parameters": parse_qs(urlparse(args.map).query),
                   "scope": "raw acquisition only; overrides, modifiers and combat are not evaluated"}
        write_json(snapshot.path / "summary.json", summary)
        snapshot.finish(backend=backend, selected_map_id=node["id"], level_id=level_id)
        print(json.dumps({"output": str(snapshot.path), "map_id": node["id"], "groups": groups,
                          "grid": [summary["rows"], summary["columns"]],
                          "selection_status": selection["status"], "status": snapshot.manifest["status"]}, ensure_ascii=False))
        return 0
    except Exception as error:
        if snapshot:
            snapshot.fail(error)
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
