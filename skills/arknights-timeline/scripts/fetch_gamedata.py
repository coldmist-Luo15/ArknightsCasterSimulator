"""Download requested CN game-data tables at one pinned Git commit."""
import argparse
import json
import re
import sys
from urllib.parse import quote

from source_io import Snapshot

REPO = "Kengxxiao/ArknightsGameData"
TABLES = {"character": "excel/character_table.json", "skill": "excel/skill_table.json",
          "uniequip": "excel/uniequip_table.json", "battle_equip": "excel/battle_equip_table.json",
          "range": "excel/range_table.json", "stage": "excel/stage_table.json",
          "enemy": "levels/enemydata/enemy_database.json", "trap": "excel/character_table.json",
          "handbook": "excel/enemy_handbook_table.json",
          "buff": "battle/buff_template_data.json", "battle_misc": "battle/battle_misc_table.json",
          "elemental": "battle/ep_breakbuff_table.json"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--ref", default="master")
    parser.add_argument("--tables", nargs="+", choices=TABLES,
                        default=["character", "skill", "uniequip", "battle_equip", "range"])
    args = parser.parse_args()
    snapshot = None
    try:
        snapshot = Snapshot(args.out, "arknights_cn_gamedata")
        if re.fullmatch(r"[0-9a-fA-F]{40}", args.ref):
            commit = args.ref.lower()
        else:
            metadata = snapshot.fetch("https://api.github.com/repos/" + REPO + "/commits/" + quote(args.ref, safe=""),
                                      "commit.response.json")
            commit = metadata["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Could not resolve a valid commit")
        snapshot.manifest.update(repo=REPO, requested_ref=args.ref, commit=commit, server="zh_CN")
        snapshot.flush()
        for name in dict.fromkeys(args.tables):
            url = f"https://raw.githubusercontent.com/{REPO}/{commit}/zh_CN/gamedata/{TABLES[name]}"
            data = snapshot.fetch(url, name + ".json")
            if not isinstance(data, dict) or not data:
                raise ValueError("Unexpected/empty game-data table: " + name)
        snapshot.finish(tables=list(dict.fromkeys(args.tables)), scope="raw tables; no roster or battle calculated")
        print(json.dumps({"output": str(snapshot.path), "commit": commit, "tables": args.tables,
                          "status": snapshot.manifest["status"]}, ensure_ascii=False))
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
