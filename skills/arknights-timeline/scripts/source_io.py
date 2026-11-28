"""Standard-library source snapshots; preserve responses, URLs and hashes."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Snapshot:
    def __init__(self, directory, kind):
        self.path = Path(directory).resolve()
        self.path.mkdir(parents=True, exist_ok=False)
        self.manifest = {"kind": kind, "started_at": now(), "status": "fetching", "sources": []}
        self.flush()

    def flush(self):
        write_json(self.path / "manifest.json", self.manifest)

    def fetch(self, url, filename, body=None, as_json=True):
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"User-Agent": "ArknightsTimelineSourceCollector/1.0"}
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = Request(url, data=payload, headers=headers)
        with urlopen(req, timeout=40) as response:
            content = response.read(100 * 1024 * 1024 + 1)
            if len(content) > 100 * 1024 * 1024:
                raise ValueError("Response exceeds 100 MiB: " + url)
            record = {"url": url, "resolved_url": response.url, "method": req.get_method(),
                      "retrieved_at": now(), "bytes": len(content),
                      "sha256": hashlib.sha256(content).hexdigest(), "file": filename}
        if body is not None:
            record["request_body"] = body
        (self.path / filename).write_bytes(content)
        self.manifest["sources"].append(record)
        self.flush()
        return json.loads(content) if as_json else content.decode("utf-8-sig")

    def finish(self, **details):
        self.manifest.update(details)
        self.manifest.update(status="retrieved_not_simulated", finished_at=now())
        self.flush()

    def fail(self, error):
        self.manifest.update(status="failed", error=str(error), finished_at=now())
        self.flush()


def unwrap(value):
    if isinstance(value, dict) and value.get("error"):
        raise ValueError("Source returned error: " + str(value["error"]))
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value
