"""Resume seed-by-candidate comparisons after an interrupted resource load."""
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pinned_inputs import warm

def run_rows(path, simulate, jobs, workers):
    pending={(i,b,m):(i,s,b,m) for i,s,b,m in jobs}
    if path.exists():
        for line in path.read_bytes().splitlines():
            if line.endswith(b'}'):
                v=json.loads(line)
                pending.pop((v['i'],v['battle'],v['map']),None)
    with path.open('a',encoding='utf8',buffering=1) as log:
        for attempt in range(6):
            if not pending:return
            with ProcessPoolExecutor(max(1,workers-attempt//2),initializer=warm) as pool:
                it=iter(pending.copy().items());futures={}
                def enqueue():
                    item=next(it,None)
                    if item:
                        key,job=item;futures[pool.submit(simulate,job)]=key
                try:
                    for _ in range(2*workers):enqueue()
                    while futures:
                        future=next(as_completed(futures))
                        key=futures.pop(future);v=future.result()
                        assert key==(v['i'],v['battle'],v['map'])
                        print(json.dumps(v),file=log);pending.pop(key)
                        enqueue()
                except Exception as exc:
                    print(f'{type(exc).__name__}: {exc}; remaining {len(pending)}',flush=True)
                    for future in futures:future.cancel()
                    if attempt==5:raise
