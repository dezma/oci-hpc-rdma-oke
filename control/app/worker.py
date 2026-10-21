from __future__ import annotations

import json
import time
import threading
from dataclasses import replace

from .config import load_settings
from .db import claim_next, complete, event, fail, heartbeat, init_db, progress
from .jobs import execute


def main() -> int:
    init_db()
    while True:
        job = claim_next()
        if not job:
            time.sleep(1.5)
            continue
        job_id = job["id"]
        action = job["action"]
        try:
            # Load operator-editable RDMA settings for every job so API and durable
            # worker processes converge without requiring a service restart.
            settings = load_settings()
            event(job_id, f"Starting {action}")
            progress(job_id, "Starting operation", 0, 1)
            stop = threading.Event()
            def renew():
                while not stop.wait(30):
                    heartbeat(job_id, 180)
            heartbeat(job_id, 180)
            lease = threading.Thread(target=renew, daemon=True)
            lease.start()
            try:
                frozen = (job.get("payload") or {}).get("settings", {})
                if frozen:
                    settings = replace(settings, **frozen)
                result = execute(settings, action, job.get("payload") or {}, job_id)
                complete(job_id, result)
            finally:
                stop.set()
                lease.join(timeout=2)
        except Exception as exc:
            # Never expose Python tracebacks or environment content through the API record.
            try:
                detail = json.loads(str(exc))
                message = json.dumps(detail)[:4000]
            except Exception:
                message = str(exc)[:4000]
            fail(job_id, message)
        time.sleep(0.2)


if __name__ == "__main__":
    raise SystemExit(main())
