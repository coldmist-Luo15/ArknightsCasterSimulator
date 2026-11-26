"""Player card stock, deployment slots and the natural cost clock on a live stage."""
from collections import defaultdict

from .fixed import ONE
from .volley_damage import _raw_f32
from .natural_cost import NaturalCostClock
from .opening_cards import tile_permits_card


class DeploymentField:
    def __init__(self,tables,opening,scene):
        self.scene=scene
        self.occupied={(u["position"]["row"],u["position"]["col"]) for u in scene.predefines}
        self.initial=opening["initial"]
        self.free_slots=self.initial["free_slots"]
        self.deploy_counts={}
        self.available_after={}
        self.dp = self.initial["cost"]
        self.cap = self.initial["cost_cap"]
        clock = opening["clock"]
        self.order = clock.get("cost_input_order", "unresolved")
        self.timer = NaturalCostClock(self.initial["cost_interval_seconds"], tables.fixed_timestep(),
                                      clock["cost_model"], clock["first_cost_frame"])

    def gain(self, frame):
        if self.dp < self.cap:
            self.dp = min(self.cap, self.dp + self.timer.advance(frame))

    def before_objects(self, frame):
        if self.order != "input_before_cost":
            self.gain(frame)

    def deployment_cost(self,uid):
        count=self.deploy_counts.get(uid,0)
        return self.initial["cards"][uid]["cost"]*(2+min(2,count))//2

    def place_operator(self,placement,extra_occupied=()):
        uid=placement["operator_id"]
        card=self.initial["cards"][uid]
        cell=placement["row"],placement["col"]
        cost=self.deployment_cost(uid)
        if placement["frame"]<self.available_after.get(uid,0):
            return "redeploy_cooldown"
        if cell in self.occupied or cell in extra_occupied:
            return "tile_occupied"
        if cost>self.dp:
            return "insufficient_cost"
        if card["slots"]>self.free_slots:
            return "deployment_limit"
        grid=self.scene.level["mapData"]["map"]
        if not (0<=cell[0]<len(grid) and 0<=cell[1]<len(grid[0])):
            return "outside_map"
        if (uid in (self.scene.level["excludeCharIdList"] or ()) or
                {"row":cell[0],"col":cell[1]} in (self.scene.level["tilesDisallowToLocate"] or ()) or
                not tile_permits_card(card,self.scene.tile(*cell))):
            return "illegal_tile"
        self.occupied.add(cell)
        self.deploy_counts[uid]=self.deploy_counts.get(uid,0)+1
        self.free_slots-=card["slots"]
        self.dp-=cost
        return None


class RangedReentries:
    """Apply subsequent deployments without rebuilding the stage's enemies or their effects."""
    @classmethod
    def for_request(cls,tables,stage,world,request,first):
        if any(a["operator_id"]==world.operator and a.get("kind","deploy")=="deploy" and a["frame"]>first for a in request["actions"]):
            return cls(tables,stage,world,request,first)
        return None

    def __init__(self,tables,stage,world,request,first):
        self.world=world
        self.uid=world.operator
        self.first=first
        self.orders=defaultdict(list)
        self.paid={}
        self.placed={}
        self.field=DeploymentField(tables,stage,world.scene)
        self.step=int(tables.fixed_timestep()*ONE)
        self.cooldowns={uid:(_raw_f32(c["panel_attributes"]["respawnTime"])+self.step-1)//self.step for uid,c in world.cards.items()}
        for a in request["actions"]:
            self.orders[a["frame"]].append(a)
        self.last=-1

    def advance(self,frame,log):
        deployed=None
        field=self.field
        while self.last<frame:
            self.last+=1
            f=self.last
            field.before_objects(f)
            for a in self.orders.get(f,()):
                uid=a["operator_id"]
                if a.get("kind","deploy")=="withdraw" and uid!=self.uid and uid in self.placed:
                    self.leave(f,uid,manual=True)
                if a.get("kind","deploy")!="deploy":
                    continue
                price=field.deployment_cost(uid)
                reason="already_deployed" if uid in self.placed else field.place_operator(a)
                if reason:
                    if f>self.first and uid==self.uid:
                        log.append(dict(kind="deploy_rejected",frame=f,reason=reason,operator_id=uid))
                    continue
                self.placed[uid],self.paid[uid]=a,price
                if uid==self.uid and f>self.first:
                    w=self.world
                    w.hp=_raw_f32(w.cards[uid]["panel_attributes"]["maxHp"])
                    w.present,w.deployment_frame=True,f
                    if w.incoming:
                        w.incoming.units[uid]=dict(w.unit_templates[uid],placement=dict(a),hp=w.hp)
                        w.incoming.move_holder(uid,a["row"],a["col"])
                    tile=w.tiles[a["row"],a["col"]]
                    deployed=dict(a,tile_z=tile["position"]["z"]-tile["locate_height_offset"])
            if field.order=="input_before_cost":
                field.gain(f)
        return deployed

    def leave(self,frame,uid,*,manual):
        if uid not in self.placed:
            return
        field=self.field
        row=self.placed.pop(uid)
        field.occupied.discard((row["row"],row["col"]))
        card=field.initial["cards"][uid]
        field.free_slots+=card["slots"]
        field.available_after[uid]=frame+self.cooldowns[uid]
        if manual:
            field.dp=min(field.cap,field.dp+min(card["cost"],self.paid[uid]//2))
