"""Compare hand-built E2M3 seven-caster timelines on the normal stage clock."""
import json
import sys
from copy import deepcopy
from pathlib import Path
from time import perf_counter

WORKBENCH = Path(__file__).resolve().parent
inputs = WORKBENCH / "inputs"
here = WORKBENCH / "out"
here.mkdir(exist_ok=True)
sys.path.insert(0, str(WORKBENCH.parents[1]))
from arkengine.caster_stage import run_caster_stage
from arkengine.opening_scene import PinnedTables
from arkengine.recal_battle import RecalBattle

tables = PinnedTables()
base = json.loads((inputs / 'nov27-bait-clear-tail-request.json').read_bytes())
S, W, M, E, B, L, T = [pick['operator_id'] for pick in base['squad']]
J = 'trap_006_antidr'
A = 'char_4229_aphris'
N = 'char_4146_nymph'
face = {'l':'LEFT','r':'RIGHT','u':'UP','d':'DOWN'}

def pos(tile):
    return dict(row=ord(tile[0])-65, col=int(tile[1:])-1)

def put(f, uid, tile, direction):
    return dict(frame=f, operator_id=uid, direction=face[direction], **pos(tile))

def cast(f, uid):
    return dict(frame=f, operator_id=uid, kind='skill')

def out(f, uid):
    return dict(frame=f, operator_id=uid, kind='withdraw')

def relay(f, key, tile, direction):
    return dict(put(f,A,tile,direction), kind='relay_deploy', relay=key)

def plan(source=base, drop=(), add=(), runes=None, squad=None):
    q = deepcopy(source)
    q['actions'] = sorted([a for a in q['actions'] if a['frame'] not in drop]+list(add),key=lambda a:a['frame'])
    for old,new in (runes or {}).items():
        q['recal_rune_ids'][q['recal_rune_ids'].index(old)] = new
    if squad:
        for old, pick in squad.items():
            i=next(i for i,v in enumerate(q['squad']) if v['operator_id']==old)
            q['squad'][i]=pick
    return q

def load(name):
    path = inputs / (name+'-request.json')
    return json.loads((path if path.is_file() else here / path.name).read_bytes())

def analyze(d):
    es=d['events']; leaks=[(e['target'],e['enemy'],round(e['hp'])) for e in es if e['kind']=='enemy_escaped'];
    fails=[(e['frame'],e.get('operator_id','').split('_')[-1],e['kind'],e.get('reason') or e.get('outcome'),e.get('sp')) for e in es if e['kind'].endswith('rejected') or e['kind']=='skill_button' and e.get('outcome') and e['outcome'] not in ('cast','accepted','use_skill','started')];
    print('stop',d['stop'],d['through_frame'],'kills',d['kills'],'of',d['scheduled'],'leak',leaks,'dp',d['dp'],'deaths',[(e['frame'],e.get('operator_id','').split('_')[-1]) for e in es if e['kind'] in ('operator_defeated','token_gained')], 'rejected',fails,flush=True)

class BrokenLine(Exception):
    pass

def run(name, q, track=False, required=None):
    t=perf_counter()
    (here/(name+'-request.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2),encoding='utf8')
    if track or required:
        b=RecalBattle(tables,q)
        old=b.hostile.objects; trace=[]
        def sample():
            old()
            if b.frame % 120 == 0:
                trace.append([b.frame,b.dp,[(u.split('_')[-1],a.row,a.col,round(a.hp/2**32),getattr(getattr(getattr(a.handler,'gauge',None),'state',None),'points',None)) for u,a in b.operators.items()],[[e.id,e.key.split('_',2)[-1],round(e.row,2),round(e.col,2),round(e.hp/2**32)] for e in b.live.values() if not e.cursor.hidden]])
        if track:
            b.hostile.objects=sample
        if required:
            leave=b.leave
            def check(actor,kind):
                hit=b.events[-1]
                leave(actor,kind)
                for uid,start,deadline,job in required:
                    if kind!='operator_defeated' or actor.uid!=uid or not start<=b.frame<deadline:
                        continue
                    if uid!=J and b.ready_at[uid]<=deadline:
                        continue
                    source=hit.get('source')
                    lock=next((e for e in reversed(b.events) if e['kind']=='enemy_attack_start' and e['source']==source and actor.key in e['targets']),None)
                    raise BrokenLine(dict(frame=b.frame,operator_id=uid,source=source,next_required_frame=deadline,job=job,enemy_attack=lock,hit=hit,ready_again=b.ready_at.get(uid),kills=len(b.finishes)))
            b.leave=check
        try:
            d=b.run()
        except BrokenLine as exc:
            stopped=exc.args[0]
            (here/(name+'-pruned.json')).write_text(json.dumps(stopped,ensure_ascii=False,indent=2),encoding='utf8')
            print(name,'pruned',stopped,flush=True)
            return None
        finally:
            if track:
                (here/(name+'-trace.json')).write_text(json.dumps(trace,ensure_ascii=False),encoding='utf8')
    else:
        d=run_caster_stage(tables,q)
    assert d['score']==22 and d['squad_size']==7 and not d['combat_unresolved']
    (here/(name+'-result.json')).write_text(json.dumps(d,ensure_ascii=False),encoding='utf8')
    print(name,round(perf_counter()-t,1),end=' '); analyze(d)
    return d
