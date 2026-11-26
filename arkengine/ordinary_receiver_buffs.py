"""V079 receiver callbacks used by independent ordinary damage packets."""
from .caster_prefabs import _prefab
from .volley_damage import _raw_f32


def passive_buffs(tables,category,prefab):
    cache=("permanent_passive_buffs",category,prefab)
    if cache not in tables._prefab_data:
        parts=(_prefab(tables,category,prefab) or {}).get("components",())
        buffs=[b for c in parts if c["script"]=="PassiveBuffAbility" and c["active"] and c["enabled"]
               and not c["path"].startswith("Modes/S") for b in c["fields"].get("_buffs",())]
        tables._prefab_data[cache]=[tables.read("buff_table.json").get(b["buffKey"],b) if b.get("loadFromDB") else b for b in buffs]
    return tables._prefab_data[cache]


class ReceiverBuffs:
    def __init__(self,tables,rng):
        self.templates=tables.read("battle/buff_template_data.json")
        self.rng=rng
        self.active={}

    def attach(self,event):
        template=event["template"]
        self.active[event.get("id",template)]=dict(event)

    def damage(self,frame,source,kind,amount):
        mask={"arts":"MAGICAL","physical":"PHYSICAL","true":"REAL"}[kind]
        for key,buff in tuple(self.active.items()):
            if frame>buff.get("end_frame",frame):
                self.active.pop(key)
                continue
            actions=self.templates[buff["template"]]["eventToActions"]["ON_TAKE_DAMAGE"]
            node=actions[0]
            name=node["$type"].split("+")[-1].split(",")[0]
            masks=("NONE",mask,"PHYSICAL_AND_MAGICAL") if kind!="true" else ("NONE",mask)
            if node.get("_damageMask") not in masks or not amount:
                continue
            if name=="Evade" and self.rng.dice(buff["prob"]):
                return 0
            if name=="BlockDamage":
                if "remaining" not in buff:
                    buff["remaining"]=_raw_f32(buff["amount"])
                used=min(amount,buff["remaining"])
                amount-=used
                buff["remaining"]-=used
                if not buff["remaining"]:
                    self.active.pop(key)
        return amount
