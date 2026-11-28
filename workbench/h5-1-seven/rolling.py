"""Check when a caster can accept the next skill input."""
from study import A, B, E, L, M, S, T, W

def ready(a,f):
 h=a.handler;uid=a.uid
 if uid==B:return not h.chant and h.mana>=8 and f>=h.born
 if uid==A and h.mode!='ordinary':return False
 if uid==S and (h.active or h.cast is not None):return False
 if uid in (W,T) and h.active:return False
 if uid==L and h.end is not None and f<=h.end:return False
 if uid in (E,M,T) and h.mode is not None:return False
 if uid=='char_377_gdglow' and h.mode is not None:return False
 if uid==W and (getattr(h,'spell',None) is not None or f<h.unblock):return False
 info=a.card['skill_row']['spData']
 return h.gauge.state.points>=info['spCost'] and f>=max(getattr(h,'born_end',0),getattr(h,'born_until',0),getattr(h,'born',0) if isinstance(getattr(h,'born',0),int) else 0)
