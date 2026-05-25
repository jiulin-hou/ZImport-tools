import os
import pytest
from zimport_tools import worker, archive
from zimport_tools.store import TaskStore


class _Cfg:
    db_path = None  # set per test
    dedupe = False  # 旧 tests 走非去重路径,保持行为不变


class _CfgDedupe:
    db_path = None
    dedupe = True


def test_process_task_eml_bundle(tmp_path, monkeypatch):
    store = TaskStore(str(tmp_path / "w.db"))
    temp_dir = tmp_path / "task1"
    (temp_dir / "input").mkdir(parents=True)
    (temp_dir / "input" / "a.eml").write_bytes(b"a")
    (temp_dir / "input" / "b.eml").write_bytes(b"b")
    tid = store.create_task("u@d", "u@d", "Inbox", str(temp_dir))
    task = store.claim_next()

    monkeypatch.setattr(worker.zimbra_auth, "delegate_token",
                        lambda cfg, acct: "TOK")
    injected = []
    monkeypatch.setattr(worker.zimbra_inject, "inject_eml",
                        lambda cfg, acct, folder, tok, p: injected.append(p))

    worker.process_task(_Cfg, store, task)
    result = store.get_task(tid)
    assert result["status"] == "done"
    assert result["total"] == 2 and result["done"] == 2
    assert len(injected) == 2


def test_process_task_records_per_eml_failure(tmp_path, monkeypatch):
    store = TaskStore(str(tmp_path / "w2.db"))
    temp_dir = tmp_path / "task2"
    (temp_dir / "input").mkdir(parents=True)
    (temp_dir / "input" / "ok.eml").write_bytes(b"a")
    (temp_dir / "input" / "bad.eml").write_bytes(b"b")
    tid = store.create_task("u@d", "u@d", "Inbox", str(temp_dir))
    task = store.claim_next()

    monkeypatch.setattr(worker.zimbra_auth, "delegate_token",
                        lambda cfg, acct: "TOK")

    def fake_inject(cfg, acct, folder, tok, p):
        if "bad" in p:
            raise worker.zimbra_inject.InjectError("invalid", "boom")

    monkeypatch.setattr(worker.zimbra_inject, "inject_eml", fake_inject)
    worker.process_task(_Cfg, store, task)
    result = store.get_task(tid)
    assert result["status"] == "done"
    assert result["done"] == 1 and result["failed"] == 1
    import json
    assert json.loads(result["failures"])[0]["name"] == "bad.eml"


def test_process_task_dedup_within_batch(tmp_path, monkeypatch):
    """同一批内含两封同 Message-ID 的 eml,第二封算 skipped 而不是 done。"""
    store = TaskStore(str(tmp_path / "wd.db"))
    temp_dir = tmp_path / "taskd"
    (temp_dir / "input").mkdir(parents=True)
    (temp_dir / "input" / "a.eml").write_bytes(b"a")
    (temp_dir / "input" / "b.eml").write_bytes(b"b")
    tid = store.create_task("u@d", "u@d", "Inbox", str(temp_dir))
    task = store.claim_next()

    monkeypatch.setattr(worker.zimbra_auth, "delegate_token",
                        lambda cfg, a: "TOK")
    monkeypatch.setattr(worker.zimbra_inject, "read_message_id",
                        lambda p: "<dup@x>")  # 所有 eml 同 id
    monkeypatch.setattr(worker.zimbra_inject, "batch_existing_message_ids",
                        lambda cfg, tok, mids: set())  # 邮箱里没
    injected = []
    monkeypatch.setattr(worker.zimbra_inject, "inject_eml",
                        lambda cfg, a, f, t, p: injected.append(p))

    worker.process_task(_CfgDedupe, store, task)
    result = store.get_task(tid)
    assert result["status"] == "done"
    assert result["done"] == 1, "只第一封注入"
    assert result["skipped"] == 1, "第二封跳过"
    assert result["failed"] == 0
    assert len(injected) == 1


def test_process_task_dedup_against_mailbox(tmp_path, monkeypatch):
    """邮箱里已有同 id 的邮件,本次注入被跳过。"""
    store = TaskStore(str(tmp_path / "we.db"))
    temp_dir = tmp_path / "taske"
    (temp_dir / "input").mkdir(parents=True)
    (temp_dir / "input" / "a.eml").write_bytes(b"a")
    tid = store.create_task("u@d", "u@d", "Inbox", str(temp_dir))
    task = store.claim_next()

    monkeypatch.setattr(worker.zimbra_auth, "delegate_token",
                        lambda cfg, a: "TOK")
    monkeypatch.setattr(worker.zimbra_inject, "read_message_id",
                        lambda p: "<x@y>")
    monkeypatch.setattr(worker.zimbra_inject, "batch_existing_message_ids",
                        lambda cfg, tok, mids: set(mids))  # all exist
    monkeypatch.setattr(worker.zimbra_inject, "inject_eml",
                        lambda *a, **kw: pytest.fail("不应触发 inject"))

    worker.process_task(_CfgDedupe, store, task)
    result = store.get_task(tid)
    assert result["done"] == 0 and result["skipped"] == 1 and result["failed"] == 0


def test_inject_retries_on_transient(monkeypatch):
    """transient 错误(network:/5xx/429/408)走 retry,最终成功记为 done。"""
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    attempts = [0]

    def fake_inject(cfg, a, f, tok, p):
        attempts[0] += 1
        if attempts[0] < 2:
            raise worker.zimbra_inject.InjectError("transient", "Zimbra 临时错误 502", http_status=502)

    monkeypatch.setattr(worker.zimbra_inject, "inject_eml", fake_inject)
    worker._inject_eml_with_retry(None, "a@d", "Inbox", "T", "/p.eml")
    assert attempts[0] == 2


def test_inject_no_retry_on_permanent(monkeypatch):
    """4xx 业务错(非 408/429)不重试,立刻抛出。"""
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    attempts = [0]

    def fake_inject(cfg, a, f, tok, p):
        attempts[0] += 1
        raise worker.zimbra_inject.InjectError("permission", "无权限", http_status=403)

    monkeypatch.setattr(worker.zimbra_inject, "inject_eml", fake_inject)
    with pytest.raises(worker.zimbra_inject.InjectError):
        worker._inject_eml_with_retry(None, "a@d", "Inbox", "T", "/p.eml")
    assert attempts[0] == 1  # 没重试


def test_inject_retries_capped(monkeypatch):
    """transient 错误持续不好时,最多 1+max_retries 次,然后抛出。"""
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    attempts = [0]

    def fake_inject(cfg, a, f, tok, p):
        attempts[0] += 1
        raise worker.zimbra_inject.InjectError("network", "网络异常")

    monkeypatch.setattr(worker.zimbra_inject, "inject_eml", fake_inject)
    with pytest.raises(worker.zimbra_inject.InjectError):
        worker._inject_eml_with_retry(None, "a@d", "Inbox", "T", "/p",
                                       max_retries=2)
    assert attempts[0] == 3  # 1 + 2 retries


def test_process_task_marks_failed_on_unpack_error(tmp_path, monkeypatch):
    store = TaskStore(str(tmp_path / "w3.db"))
    temp_dir = tmp_path / "task3"
    (temp_dir / "input").mkdir(parents=True)
    tid = store.create_task("u@d", "u@d", "Inbox", str(temp_dir))
    task = store.claim_next()
    monkeypatch.setattr(worker.zimbra_auth, "delegate_token",
                        lambda cfg, acct: "TOK")

    def boom(input_dir, work_dir):
        raise ValueError("corrupt archive")

    monkeypatch.setattr(worker.archive, "normalize", boom)
    worker.process_task(_Cfg, store, task)
    result = store.get_task(tid)
    assert result["status"] == "failed"
    assert "corrupt" in result["error"]


def test_process_task_marks_failed_on_tgz_inject_error(tmp_path, monkeypatch):
    """The zimbra-export (tgz) branch must record failure if inject_tgz raises."""
    store = TaskStore(str(tmp_path / "w4.db"))
    temp_dir = tmp_path / "task_tgz"
    (temp_dir / "input").mkdir(parents=True)
    tid = store.create_task("u@d", "u@d", "Inbox", str(temp_dir))
    task = store.claim_next()

    monkeypatch.setattr(worker.zimbra_auth, "delegate_token",
                        lambda cfg, acct: "TOK")
    # Make archive.normalize return a fake "zimbra-export" Normalized
    fake = archive.NormalizedInput(kind="zimbra-export", eml_paths=[],
                              repacked_tgz=str(temp_dir / "x.tgz"))
    monkeypatch.setattr(worker.archive, "normalize", lambda *a, **kw: fake)

    def boom(cfg, acct, tok, path):
        raise worker.zimbra_inject.InjectError("transient", "Zimbra 500", http_status=500)

    monkeypatch.setattr(worker.zimbra_inject, "inject_tgz", boom)
    worker.process_task(_Cfg, store, task)
    result = store.get_task(tid)
    assert result["status"] == "failed"
    assert "500" in result["error"]


def test_process_task_clears_stale_work_dir_on_retry(tmp_path, monkeypatch):
    """If 'work/' has leftover files from a previous failed run, they must
    be wiped before normalize is called again — otherwise stale artifacts
    contaminate the new attempt."""
    store = TaskStore(str(tmp_path / "w5.db"))
    temp_dir = tmp_path / "task_retry"
    (temp_dir / "input").mkdir(parents=True)
    (temp_dir / "input" / "a.eml").write_bytes(b"a")
    stale_work = temp_dir / "work"
    stale_work.mkdir()
    (stale_work / "leftover.txt").write_bytes(b"stale junk")
    tid = store.create_task("u@d", "u@d", "Inbox", str(temp_dir))
    task = store.claim_next()

    seen_work_dirs = []

    def fake_normalize(input_dir, work_dir):
        seen_work_dirs.append(sorted(os.listdir(work_dir)))
        return archive.NormalizedInput(kind="eml-bundle",
                                  eml_paths=[os.path.join(input_dir, "a.eml")],
                                  repacked_tgz=None)

    monkeypatch.setattr(worker.archive, "normalize", fake_normalize)
    monkeypatch.setattr(worker.zimbra_auth, "delegate_token",
                        lambda cfg, acct: "TOK")
    monkeypatch.setattr(worker.zimbra_inject, "inject_eml",
                        lambda *a, **kw: None)

    worker.process_task(_Cfg, store, task)
    assert seen_work_dirs == [[]], "work dir should be empty when normalize runs"
