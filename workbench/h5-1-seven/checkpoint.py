"""Branch a search battle at an after-tick boundary. Final verification always starts at F0."""
import types
from collections import defaultdict
from copy import deepcopy
from study import RecalBattle, load, run_caster_stage, tables

class Checkpoint:
    def __init__(self, q, start):
        self.start=start
        original=dict(q,end_frame=start-1)
        self.prefix=RecalBattle(tables,original)
        report=self.prefix.run()
        self.playable=report['stop']=='requested_frame'
        self.prefix_report=report

    def node(self, actions, end=None):
        source=self.prefix
        shared=[source.tables,source.scene,source.cards,source.navigation,source.clocks,
                source.schedule,source.tiles,*source.tables._cache.values()]
        if isinstance(source.snow,types.ModuleType):
            shared.append(source.snow)
        for h in source.handlers:
            for key in ('ev','evidence','card','characters'):
                item=getattr(h,key,None)
                if item is not None:
                    shared.append(item)
        memo={id(o):o for o in shared}
        memo[id(source.events)]=list(source.events)
        b=deepcopy(source,memo)
        b.frame=self.start
        b.actions=defaultdict(list)
        for a in actions:
            if a['frame']>=self.start:
                b.actions[a['frame']].append(a)
        b.request={k:v for k,v in b.request.items() if k!='end_frame'}
        b.request['actions']=actions
        if end is not None:
            b.request['end_frame']=end
        return b
