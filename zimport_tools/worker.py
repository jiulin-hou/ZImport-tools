import os
import re
import sys
import time
import shutil
import threading

from zimport_tools import archive, zimbra_auth, zimbra_inject
from zimport_tools.config import Config
from zimport_tools.store import TaskStore


# 仅对 transient 错误 retry —— 网络抖、Zimbra 临时 5xx、限流 429/408
_TRANSIENT_RE = re.compile(r"^network:|HTTP 5\d\d|HTTP 429|HTTP 408")


def _inject_eml_with_retry(cfg, account, folder, token, path,
                            max_retries=2):
    """Inject one eml with up to max_retries additional attempts on
    transient errors (1.5s, 2.25s backoff). Non-transient errors raise
    immediately."""
    for attempt in range(max_retries + 1):
        try:
            zimbra_inject.inject_eml(cfg, account, folder, token, path)
            return
        except zimbra_inject.InjectError as exc:
            if attempt == max_retries or not _TRANSIENT_RE.search(str(exc)):
                raise
            time.sleep(1.5 ** (attempt + 1))


def process_task(cfg, store, task):
    tid = task["id"]
    try:
        # retry 时 work 可能残留旧产物,清掉再 normalize 一遍
        work = os.path.join(task["temp_dir"], "work")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        norm = archive.normalize(os.path.join(task["temp_dir"], "input"), work)
        store.set_status(tid, "running", kind=norm.kind)
        token = zimbra_auth.delegate_token(cfg, task["account"])

        if norm.kind == "zimbra-export":
            store.set_totals(tid, 1)
            # tgz 自带 resolve=skip,Zimbra 内部按 Message-ID 去重
            zimbra_inject.inject_tgz(cfg, task["account"], token,
                                     norm.repacked_tgz)
            store.update_progress(tid, done=1, failed=0)
        else:
            store.set_totals(tid, len(norm.eml_paths))
            done = failed = skipped = 0
            failures = []
            seen_local = set()  # 同批内重复(同 Message-ID)
            for path in norm.eml_paths:
                name = os.path.basename(path)
                try:
                    if cfg.dedupe:
                        mid = zimbra_inject.read_message_id(path)
                        if mid:
                            if mid in seen_local:
                                skipped += 1
                                failures.append({"name": name,
                                                 "reason": "duplicate (same batch)"})
                                store.update_progress(tid, done=done,
                                                      failed=failed,
                                                      skipped=skipped)
                                continue
                            seen_local.add(mid)
                            if zimbra_inject.message_exists(cfg, token, mid):
                                skipped += 1
                                failures.append({"name": name,
                                                 "reason": "duplicate (already in mailbox)"})
                                store.update_progress(tid, done=done,
                                                      failed=failed,
                                                      skipped=skipped)
                                continue
                    _inject_eml_with_retry(cfg, task["account"],
                                           task["target_folder"],
                                           token, path)
                    done += 1
                except zimbra_inject.InjectError as exc:
                    failed += 1
                    failures.append({"name": name, "reason": str(exc)})
                store.update_progress(tid, done=done, failed=failed,
                                      skipped=skipped)
            store.set_failures(tid, failures)
        store.set_status(tid, "done")
    except Exception as exc:  # noqa: BLE001
        # Catch everything: any uncaught exception here (zimbra unreachable,
        # disk full, malformed eml, etc.) must be recorded against THIS task
        # and not bubble up to crash the worker loop — the next claim_next
        # would otherwise leave the user stuck on a permanently "running"
        # task.
        store.set_status(tid, "failed", error=str(exc))


def _purge(cfg, store):
    for temp_dir in store.purge_old(cfg.retention_days):
        shutil.rmtree(temp_dir, ignore_errors=True)


def _loop(cfg, store):
    last_purge = 0.0
    while True:
        task = store.claim_next()
        if task is None:
            now = time.time()
            if now - last_purge > 3600:
                _purge(cfg, store)
                last_purge = now
            time.sleep(2)
            continue
        process_task(cfg, store, task)


def main():
    cfg = Config(sys.argv[1] if len(sys.argv) > 1 else "config.ini")
    store = TaskStore(cfg.db_path)
    store.recover_interrupted()
    threads = [threading.Thread(target=_loop, args=(cfg, store), daemon=True)
               for _ in range(cfg.concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
