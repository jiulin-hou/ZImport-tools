import json
import os
import sys
import time
import shutil
import threading

from zimport_tools import archive, zimbra_auth, zimbra_inject
from zimport_tools.config import Config
from zimport_tools.store import TaskStore


# 仅对 transient 错误 retry —— 网络抖、Zimbra 临时 5xx、限流 429/408
_TRANSIENT_CODES = {"network", "transient"}


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
            if attempt == max_retries or exc.code not in _TRANSIENT_CODES:
                raise
            time.sleep(1.5 ** (attempt + 1))


def process_task(cfg, store, task):
    tid = task["id"]
    raw_keep = task.get("keep_files")
    keep_set = set()
    if raw_keep:
        try:
            keep_set = set(json.loads(raw_keep))
        except (TypeError, ValueError):
            keep_set = set()
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
            # tgz 走 Zimbra 内部一次性 import,中途无法被 cancel —
            # 进 inject 之前做最后一次检查,给 queued 期间取消的用户机会。
            if store.cancel_requested(tid):
                store.set_status(tid, "cancelled")
                return
            # tgz 自带 resolve=skip,Zimbra 内部按 Message-ID 去重
            zimbra_inject.inject_tgz(cfg, task["account"], token,
                                     norm.repacked_tgz)
            store.update_progress(tid, done=1, failed=0)
        else:
            # If keep_set is non-empty, process only those basenames
            # (used by "retry only failed"); otherwise process all.
            if keep_set:
                paths = [p for p in norm.eml_paths
                         if os.path.basename(p) in keep_set]
            else:
                paths = list(norm.eml_paths)
            store.set_totals(tid, len(paths))
            done = failed = skipped = 0
            failures = []
            seen_local = set()  # 同批内重复(同 Message-ID)

            # Batch-check Zimbra mailbox for existing Message-IDs upfront so
            # we avoid one SOAP round-trip per eml. The check itself can
            # fail (network, Zimbra Fault); we keep those mids as
            # "undecidable" and tag those messages in the failures log so
            # the user knows their dedupe verdict is best-effort.
            mailbox_existing = set()
            undecidable = set()
            if cfg.dedupe:
                path_to_mid = {p: zimbra_inject.read_message_id(p)
                               for p in paths}
                all_mids = [m for m in path_to_mid.values() if m]
                mailbox_existing, undecidable = (
                    zimbra_inject.batch_existing_message_ids(
                        cfg, token, all_mids))
            else:
                path_to_mid = {}

            for path in paths:
                if store.cancel_requested(tid):
                    store.set_failures(tid, failures)
                    store.set_status(tid, "cancelled")
                    return
                name = os.path.basename(path)
                try:
                    if cfg.dedupe:
                        mid = path_to_mid.get(path) or ""
                        if not mid:
                            # No Message-ID header → we cannot dedupe this
                            # message. Inject it anyway but record a warning
                            # so the user can spot the un-dedupable mail.
                            failures.append({
                                "name": name,
                                "code": "no_message_id",
                                "reason": "缺 Message-ID 头,无法判重已直接导入"})
                        else:
                            if mid in seen_local:
                                skipped += 1
                                failures.append({"name": name,
                                                 "code": "duplicate_batch",
                                                 "reason": "重复(本批内同 Message-ID)"})
                                store.update_progress(tid, done=done,
                                                      failed=failed,
                                                      skipped=skipped)
                                continue
                            seen_local.add(mid)
                            if mid in mailbox_existing:
                                skipped += 1
                                failures.append({"name": name,
                                                 "code": "duplicate_mailbox",
                                                 "reason": "重复(邮箱内已存在)"})
                                store.update_progress(tid, done=done,
                                                      failed=failed,
                                                      skipped=skipped)
                                continue
                            if mid in undecidable:
                                # Dedupe lookup itself errored; inject but
                                # warn so the user can audit afterwards.
                                failures.append({
                                    "name": name,
                                    "code": "dedupe_check_failed",
                                    "reason": "判重查询出错,已直接导入,建议在 Zimbra Web 中手工确认"})
                    _inject_eml_with_retry(cfg, task["account"],
                                           task["target_folder"],
                                           token, path)
                    done += 1
                except zimbra_inject.InjectError as exc:
                    failed += 1
                    failures.append({"name": name,
                                     "code": exc.code,
                                     "reason": exc.message_zh})
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
