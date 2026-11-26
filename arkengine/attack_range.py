"""Contact between an ordinary oriented grid range and a projected enemy's root circle."""


# V079 OverlapBox initializes the query polygon with this radius. The enemy
# circle already carries its own prefab radius. See tools/verify_overlap_box.py.
BOX_QUERY_RADIUS = 0.009999999776482582
OVERLAP_DISTANCE_TOLERANCE = 10 * 2**-23


def range_cells(data, placement, rows, cols):
    if data["direction"] != 1:
        raise ValueError("基础范围的参考朝向尚未支持")
    turns = ("RIGHT", "UP", "LEFT", "DOWN").index(placement["direction"])
    cells = []
    for offset in data["grids"]:
        dr, dc = offset["row"], offset["col"]
        for _ in range(turns):
            dr, dc = dc, -dr
        row, col = placement["row"] + dr, placement["col"] + dc
        if 0 <= row < rows and 0 <= col < cols:
            cells.append((row, col))
    return cells


def circle_in_range(row, col, radius, cells, *, box_query=False):
    """Check ideal cells or the Unity OverlapArea query used by a box selector."""
    limit = radius + BOX_QUERY_RADIUS + OVERLAP_DISTANCE_TOLERANCE if box_query else radius
    limit_squared = limit * limit
    for r, c in cells:
        dy = max(abs(row - r) - 0.5, 0)
        dx = max(abs(col - c) - 0.5, 0)
        distance_squared = dx * dx + dy * dy
        if distance_squared < limit_squared or (not box_query and distance_squared == limit_squared):
            return True
    return False


class RangeContacts:
    def __init__(self):
        self.positions = {}
        self.inside = set()
        self.unknown = set()
        self.cells = []
        self.report = dict(scope="first_fragment_ordinary_base_range_geometry", range_id=None,
                           cells=[], first_contact=None, unknown_instances={})

    def deploy(self, tables, card, placement, rows, cols):
        unit = tables.read("excel/character_table.json")[card["operator_id"]]
        key = unit["phases"][card["elite"]]["rangeId"]
        if card.get("module_ids") and unit.get("subProfessionId")=="splashcaster":
            from .caster_modules import splash_range_module
            module = splash_range_module(tables,card)
            if module:
                self.report["module_range_override"] = module["module_id"]
                key = module["range_id"]
            elif card["module"]=="unknown" and any(tables.read("excel/uniequip_table.json")["equipDict"][mid]["typeIcon"]=="spc-x"
                                                   for mid in card["module_ids"]):
                self.report["scope"] = "ordinary_base_range_conditional_on_no_module_range_extension"
        data = tables.read("excel/range_table.json")[key]
        self.cells = range_cells(data, placement, rows, cols)
        self.report.update(operator_id=card["operator_id"], range_id=key,
                           cells=[dict(row=r, col=c) for r, c in self.cells])

    def update(self, frame, kind, fields):
        key = fields["instance_id"]
        if kind in ("free_motion_sample", "exit_motion_sample", "post_wait_motion_sample"):
            self.positions[key] = (fields["row"], fields["col"])
        elif kind == "checkpoint_begin":
            pos = fields["position"]
            self.positions[key] = None if fields["disappeared"] else (pos["row"], pos["col"])
        elif kind == "range_motion_unresolved":
            self.unknown.add(key)
            self.inside.discard(key)
            self.report["unknown_instances"][key] = frame
            self.positions.pop(key, None)

    def evaluate(self, loop, radii):
        for key, pos in self.positions.items():
            inside = pos is not None and circle_in_range(*pos, radii[key], self.cells, box_query=True)
            if inside == (key in self.inside):
                continue
            if inside:
                self.inside.add(key)
                if self.report["first_contact"] is None:
                    self.report["first_contact"] = dict(frame=loop.frame, instance_id=key,
                        earlier_tracks_complete=not self.unknown)
            else:
                self.inside.remove(key)
            loop.emit("attack_range_enter" if inside else "attack_range_exit",
                      instance_id=key, operator_id=self.report["operator_id"],
                      position=None if pos is None else dict(row=pos[0], col=pos[1]))
