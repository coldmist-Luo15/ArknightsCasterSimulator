"""Lin PLX-Y's per-mode collision aura, sampled when her damage modifier is emitted."""
from bisect import bisect_right
from math import hypot

from .attack_range import BOX_QUERY_RADIUS, OVERLAP_DISTANCE_TOLERANCE, RangeContacts
from .caster_prefabs import caster_prefab_evidence
from .damage import HP_EPSILON
from .unity_box_contact import centered_range_boxes,range_sensor_contact


def trigger_contact(pos,radius,cells,*,verified_box_sensor=False):
    distance = min((hypot(max(abs(pos[0]-r)-.5,0),max(abs(pos[1]-c)-.5,0)) for r,c in cells),
                   default=float("inf"))
    delta = distance-radius
    if verified_box_sensor:
        # Native BoxCollider2D uses the default contact offset when edgeRadius
        # is zero. Trigger contacts and overlap queries share b2TestOverlap.
        delta -= BOX_QUERY_RADIUS + OVERLAP_DISTANCE_TOLERANCE
        return None if abs(delta)<=1e-5 else delta<0
    if delta < -1e-5:
        return True
    if delta > BOX_QUERY_RADIUS+1e-5:
        return False
    return None


def device_contacts(tables,scene,cells,*,contact_solver=None):
    sure,uncertain = [],[]
    for unit in scene.predefines:
        key = unit["inst"]["characterKey"]
        if key!="trap_124_eradio":
            uncertain.append(unit["alias"])
            continue
        shape = tables._component_fields("[uc]tokens",key,("m_Radius","m_Offset","m_Enabled","m_IsTrigger"),
                                         "lin_target_device_radius",component_type="CircleCollider2D")
        pos = (unit["position"]["row"],unit["position"]["col"])
        if not shape["m_Enabled"]:
            continue
        if any(shape["m_Offset"].values()):
            uncertain.append(unit["alias"])
            continue
        contact=(contact_solver(pos,shape["m_Radius"]) if contact_solver else trigger_contact(pos,shape["m_Radius"],cells))
        if contact is True:
            sure.append(unit["alias"])
        elif contact is None:
            uncertain.append(unit["alias"])
    return sure,uncertain


def verify_aura(tables,card):
    ev = caster_prefab_evidence(tables,card["operator_id"],card.get("skill",3),skill_level=card["skill_level"],
                               module_id=card["module_ids"][0],module_level=card["module_level"])
    matches = [v for v in ev["equipment"].values()
               if any(c["script"]=="AuraAbility" for c in v["components"])]
    equip, = matches
    def component(name):
        row, = [x for x in equip["components"] if x["script"]==name and not x["path"]]
        return row
    talent = component("Talent")["fields"]
    ability = component("AuraAbility")["fields"]
    shape = component("AutoLoadBoxRange")["fields"]
    validator = component("TargetValidator")
    opts = validator["fields"]["_targetOptions"]
    buff, = ability["_buffs"]
    if (talent["_overwriteTalentKey"]!="10" or talent["_applyTalentRange"] or ability["_selfOption"] or
            ability["_targetValidator"]["m_PathID"]!=validator["id"] or shape["_useBlackboardRangeId"] or
            shape["_extendable"] or not ability["_removeBuffWhenTargetLeave"] or
            not ability["_removeBuffWhenAbilityDetached"] or ability["_onlyAddBuffOnceForEachTarget"] or
            ability["_passiveBuffs"] or ability["_selector"]["m_PathID"] or
            buff["templateKey"]!="lin_e_002_trait" or
            (opts["targetSide"],opts["targetMotion"],opts["targetCategory"])!=(2,3,1) or
            any(v for k,v in opts.items() if k not in {"targetSide","targetMotion","targetCategory","enableAdvancedOptions"})):
        raise ValueError("林 Y 模组光环的范围、挂载或目标过滤器发生变化")
    return dict(source=equip["source"],range_component="AutoLoadBoxRange",mode_talent_key="10",
                target_side=opts["targetSide"],target_motion=opts["targetMotion"],target_category=opts["targetCategory"])


class LinStageAura:
    """Current PLX-Y recipient buffs for an ordinary battle with reviewed enemy roots."""
    def __init__(self,tables,wave,placement,range_data):
        self.tables,self.wave=tables,wave
        self.placement=placement
        self.set_range(range_data)
        self.contacts = {}

    def set_range(self,range_data):
        self.boxes=centered_range_boxes(range_data,self.placement)

    def detach(self):
        changed = bool(self.contacts)
        self.contacts.clear()
        return changed

    def advance(self, frame):
        # Physics queues enter/exit after previously registered attacks for this
        # tick. The next OBJECTS pass sees these attachments. A unit created in
        # this tick's COROUTINES was absent during Physics.
        wave = self.wave
        contacts={}
        for key in wave.active[frame]:
            if wave.births[key]["frame"]>=frame or wave.attrs[key]["hp_q32"]<=HP_EPSILON:
                continue
            if getattr(wave,"flags",None) and not wave.targetable(key,frame):
                continue
            position=wave.positions[frame][key]
            radius=wave.radii[key]
            contact=range_sensor_contact(self.tables,position,radius,self.boxes)
            if contact is None and getattr(wave,"continuous",False):
                contact=any(hypot(max(abs(position[0]-r)-h*.5,0),max(abs(position[1]-c)-w*.5,0))
                            <=radius+BOX_QUERY_RADIUS+OVERLAP_DISTANCE_TOLERANCE for r,c,h,w in self.boxes)
                wave.assumptions.append(dict(kind="rule_applied",frame=frame,target=key,
                                             rule="circle_box_overlap_with_contact_offset",touching=contact))
            if contact is not False:
                contacts[key]=contact
        changed = contacts != self.contacts
        self.contacts = contacts
        return changed

    def remove(self, key):
        return self.contacts.pop(key,False) is not False

    def members(self):
        # Finish synchronously removes the enemy's recipient buff, even inside
        # a volley that applies successive packets before the next physics step.
        certain, uncertain = [], []
        for key,contact in sorted(self.contacts.items()):
            if self.wave.attrs[key]["hp_q32"]>HP_EPSILON:
                (certain if contact else uncertain).append(key)
        return certain, uncertain


class LinAuraGeometry:
    """Pre-index the final positions of each receiver OBJECTS update. Unknown motion stays unknown."""
    def __init__(self,cells,radii,spawned,history,*,contact_solver=None):
        self.cells,self.radii,self.spawned = cells,radii,spawned
        self.samples = {key:([],[]) for key in spawned}
        self.active_since = {}
        positions = RangeContacts()
        for frame,entries in sorted(history.items()):
            for kind,fields in entries:
                key = fields["instance_id"]
                if key not in spawned or kind not in {"free_motion_sample","post_wait_motion_sample","exit_motion_sample",
                        "checkpoint_begin","route_end_in_range","range_motion_unresolved"}:
                    continue
                positions.update(frame,kind,fields)
                if fields.get("active_ticks",0)>0:
                    self.active_since.setdefault(key,frame)
                pos = positions.positions.get(key)
                if kind=="route_end_in_range" or (kind=="checkpoint_begin" and fields["disappeared"]):
                    inside = False
                elif key in positions.unknown or pos is None or key not in radii:
                    inside = None
                else:
                    # The aura uses physical BoxCollider2D triggers. Contacts close to the
                    # polygon skin are retained as ambiguous without a matching physics trace.
                    inside=(contact_solver(pos,radii[key]) if contact_solver else trigger_contact(pos,radii[key],cells))
                times,values = self.samples[key]
                if not values or values[-1]!=inside:
                    times.append(frame)
                    values.append(inside)

    def at(self, frame, *, phase, entered_mode, devices, finished, unknown_devices=()):
        certain,possible = set(),set()
        first = frame-2
        last = frame if phase=="COROUTINES" else frame-1
        for key,birth in self.spawned.items():
            finish = finished.get(key)
            if birth["spawn_frame"]>=frame or finish is not None and finish<first:
                continue
            times,values = self.samples[key]
            contacts = []
            for tick in range(first,last+1):
                i = bisect_right(times,tick)-1
                contacts.append(False if tick<birth["spawn_frame"] else (values[i] if i>=0 else None))
            if all(c is False for c in contacts):
                continue
            possible.add(key)
            if (all(c is True for c in contacts) and self.active_since.get(key,frame)<=first
                    and first>entered_mode and (finish is None or finish>frame)):
                certain.add(key)
        return dict(frame=frame,phase=phase,mode_range_since=entered_mode,
                    stable_members=sorted(certain),possible_members=sorted(possible),
                    stable_devices=sorted(devices) if first>entered_mode else [],
                    possible_devices=sorted(set(devices)|set(unknown_devices)))


def count_bounds(sample, maximum, *, hp=None, ordinary_finishes=()):
    stable = set(sample["stable_members"])
    possible = set(sample["possible_members"])
    if hp is not None:
        frame = sample["frame"]
        # Ordinary enemy Finish clears recipient buffs synchronously. A rebirth
        # or a special death intercept needs its own Finish observation.
        dead = hp.dead_by(frame+1)
        possible -= set(dead).intersection(ordinary_finishes)
        stable = {key for key in stable if hp.floor_at(key,frame)>HP_EPSILON}
    return (min(maximum,len(stable)+len(sample["stable_devices"])),
            min(maximum,len(possible)+len(sample["possible_devices"])))
