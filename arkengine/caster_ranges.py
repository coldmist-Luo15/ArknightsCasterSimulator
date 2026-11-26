"""Primary active-skill grid and distinct, anchored secondary areas (bottom-origin rows)."""
from math import isfinite
from .attack_range import range_cells
from .caster_modules import splash_range_module


DIRECTION = {"RIGHT": (0, 1), "UP": (1, 0), "LEFT": (0, -1), "DOWN": (-1, 0)}
# Revisions of the operator pages whose text or annotated range diagrams resolve
# targeting geometry that the skill table's nullable display range cannot encode.
RANGE_REVISIONS = {"gdglow": 446428, "whitw2": 446481, "aphris": 446565, "marcil": 446552,
                   "pasngr": 446713, "necras": 446634}


def _point(value, rows, cols):
    r, c = value["row"], value["col"]
    if type(r) is not int or type(c) is not int or not (0 <= r < rows and 0 <= c < cols):
        raise ValueError(f"地块坐标超出地图 {r}, {c}")
    return r, c


def _aphris(card, event, origin, rows, cols, direction):
    second = card["skill"] == 2
    if event.get("area")=="sampling_aura" and second:
        if type(event.get("skill_active")) is not bool:
            raise ValueError("取样优化需要当前二技能是否生效")
        second = event["skill_active"]
    key = "allied_turns" if second else "relays"
    if key not in event:
        return dict(status="unresolved", reason=f"需要当前场上的 {key} 与朝向，空数组表示场上没有")
    turns = {}
    relay_cells = set()
    for unit in event[key]:
        cell = _point(unit, rows, cols)
        if cell in turns or unit["direction"] not in DIRECTION:
            raise ValueError("光束转向格重复或朝向无效")
        turns[cell] = unit["direction"]
        if not second or "relay" in unit:
            relay_cells.add(cell)
    cells = [origin]
    bends, extensions = [0], [0]
    limit = rows * cols if second else max(g["col"] for g in card["base_range"]["grids"])
    while len(cells) <= limit:
        dr, dc = DIRECTION[direction]
        r, c = cells[-1][0] + dr, cells[-1][1] + dc
        if not (0 <= r < rows and 0 <= c < cols) or (r, c) in cells:
            break
        cells.append((r, c))
        bend, extension = bends[-1], extensions[-1]
        if (r, c) in turns:
            new_dr, new_dc = DIRECTION[turns[r, c]]
            # Opposite directions terminate on the revisited cell; only an orthogonal turn delays a spell.
            bend += int(dr * new_dr + dc * new_dc == 0)
            direction = turns[r, c]
            if (r, c) in relay_cells:
                extension += 1
            if not second and (r, c) in relay_cells:
                limit += 2
        bends.append(bend)
        extensions.append(extension)
    return dict(status="resolved", shape="redirectable_line", cells=cells, contact="enemy_tile_coordinate",
                dir_change_times=bends, extend_times=extensions)


def skill_range_cells(tables, loadout, event):
    """Geometry of the chosen event while the skill is active; caller handles eligibility and priority."""
    rows, cols = event["rows"], event["cols"]
    if any(type(n) is not int or n <= 0 for n in (rows, cols)):
        raise ValueError("地图行列数须为正整数")
    placement = event["placement"]
    origin = _point(placement, rows, cols)
    direction = placement["direction"]
    if direction not in DIRECTION:
        raise ValueError("无效的部署朝向")
    sid = loadout["skill_id"]
    op = loadout["operator_id"].split("_")[-1]
    revision = RANGE_REVISIONS.get(op)
    source = {"prts": f"https://prts.wiki/index.php?oldid={revision}"} if revision else {}
    result = dict(skill_id=sid, **source)
    part = event.get("area", "primary")
    if part == "storm":
        if sid != "skchr_pasngr_3":
            raise ValueError("雷暴区域仅适用于异客三技能")
        if "target_tile" not in event:
            return dict(**result, status="unresolved", reason="需要命中目标的判定中心所在格")
        center = _point(event["target_tile"], rows, cols)
        shape = tables.read("excel/range_table.json")["x-1"]
        cells = range_cells(shape, dict(row=center[0], col=center[1], direction="RIGHT"), rows, cols)
        return dict(**result, status="resolved", shape="storm_area", cells=cells)
    if sid == "skchr_necras_1" and part in ("primary", "servant_blast"):
        radius, = (v["value"] for v in loadout["skill_row"]["blackboard"] if v["key"] == "range_radius")
        center = event.get("servant_center")
        if center is None:
            return dict(**result, status="unresolved", radius=radius, reason="需要本次生成或升级仆役的实际判定中心 servant_center")
        def position(p):
            rc = (p["row"], p["col"])
            if any(type(x) not in (int, float) or not isfinite(x) for x in rc):
                raise ValueError("判定中心必须是有限的连续坐标")
            return rc
        r, c = position(center)
        if not (-0.5 <= r < rows-0.5 and -0.5 <= c < cols-0.5):
            raise ValueError("仆役中心超出战场")
        answer = dict(**result, status="resolved", shape="circle", center=(r, c), radius=radius,
                      contact="enemy_center", can_hit_air=True)
        if "target_centers" in event:
            hits = {}
            for key, pt in event["target_centers"].items():
                tr, tc = position(pt)
                d2 = (tr-r)**2 + (tc-c)**2
                hits[key] = None if d2 == radius**2 else d2 < radius**2
            answer.update(center_hits=hits, boundary="exact_equality_unverified")
        return answer
    if sid == "skchr_necras_1" and part == "summon_tiles":
        return dict(**result, status="resolved", shape="summon_grid",
                    cells=range_cells(loadout["base_range"], placement, rows, cols))
    if part != "primary" and not (op=="aphris" and part=="sampling_aura"):
        raise ValueError(f"尚无该技能的独立范围 {part!r}")
    if op == "aphris":
        return {**result, **_aphris(loadout, event, origin, rows, cols, direction)}
    if sid in ("skchr_gdglow_3", "skchr_whitw2_1", "skchr_whitw2_3"):
        # S1 here is the skill's toggled-on mode; the inactive mode uses the ordinary attack.
        return dict(**result, status="resolved", shape="drone_acquisition_global",
                    cells=[(r,c) for r in range(rows) for c in range(cols)])
    if sid == "skchr_marcil_3":
        dr, dc = DIRECTION[direction]
        r, c = origin
        center = dict(row=min(max(0, r + 2*dr), rows-1), col=min(max(0, c + 2*dc), cols-1), direction="RIGHT")
        cells = range_cells(tables.read("excel/range_table.json")["x-1"], center, rows, cols)
        return dict(**result, status="resolved", shape="explosion", cells=cells, center=(center["row"], center["col"]))
    grid = loadout["skill_range"]
    if grid is None:
        grid = loadout["base_range"]
        if loadout["module_info"] and loadout["module_info"]["typeIcon"] == "spc-x":
            module = splash_range_module(tables, loadout)
            grid = tables.read("excel/range_table.json")[module["range_id"]]
    extra = 0
    if sid == "skchr_pasngr_2":
        extra, = (v["value"] for v in loadout["skill_row"]["blackboard"] if v["key"] == "ability_range_forward_extend")
    if sid == "skchr_marcil_2":
        if not isinstance(event.get("upgraded"), bool):
            return dict(**result, status="unresolved", reason="需要本次部署的使魔是否已经升级")
        extra = int(event["upgraded"])
    if sid == "skchr_marcil_1" and event.get("talent_powered", False):
        extra = 1
    if extra:
        if int(extra) != extra or extra < 0:
            raise ValueError("延伸距离不是非负格数")
        # PRTS 作战机制/攻击范围: each source cell extends forward then unions with the old cells.
        coords = {(g["row"], g["col"] + step) for g in grid["grids"] for step in range(int(extra)+1)}
        grid = dict(direction=1, grids=[dict(row=r,col=c) for r,c in sorted(coords)])
    return dict(**result, status="resolved", shape="primary_grid", cells=range_cells(grid, placement, rows, cols),
                range_id=grid.get("id"), forward_extension=extra)
