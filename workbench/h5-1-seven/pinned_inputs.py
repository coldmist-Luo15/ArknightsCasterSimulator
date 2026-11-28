"""Read the V079 sources for this seven-caster squad before opening Unity objects."""
import json
import time
from zipfile import BadZipFile
from study import inputs,tables

def warm():
    if getattr(tables,'_seven_warmed',False):
        return
    from UnityPy.helpers import TypeTreeHelper
    TypeTreeHelper.read_typetree_boost=None
    paths=json.loads((inputs/'cache-input-list.json').read_bytes())
    read_bundle=tables.verified_bundle
    def checked(read,key):
        for attempt in range(3):
            try:
                return read(key)
            except (OSError,ValueError,BadZipFile):
                if attempt==2:raise
                time.sleep(.25*(attempt+1))
    blobs={key:checked(read_bundle,key) for key in paths['bundles']}
    for key in paths['tables']:
        checked(tables.read,key)
    tables.verified_bundle=lambda key:(blobs[key][0],blobs[key][1].copy()) if key in blobs else read_bundle(key)
    tables._seven_warmed=True
