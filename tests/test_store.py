from zimport_tools.store import TaskStore


def test_create_and_get_task(tmp_path):
    store = TaskStore(str(tmp_path / "t.db"))
    tid = store.create_task(account="u@d", requester="u@d",
                            target_folder="Inbox", temp_dir="/tmp/x")
    task = store.get_task(tid)
    assert task["account"] == "u@d"
    assert task["status"] == "queued"
    assert task["done"] == 0


def test_list_tasks_filters_by_requester(tmp_path):
    store = TaskStore(str(tmp_path / "t2.db"))
    store.create_task("a@d", "admin@d", "Inbox", "/tmp/a")
    store.create_task("b@d", "admin@d", "Inbox", "/tmp/b")
    store.create_task("c@d", "other@d", "Inbox", "/tmp/c")
    assert len(store.list_tasks("admin@d")) == 2
    assert len(store.list_tasks("other@d")) == 1


def test_claim_next_is_fifo_and_marks_running(tmp_path):
    store = TaskStore(str(tmp_path / "c.db"))
    t1 = store.create_task("a@d", "a@d", "Inbox", "/tmp/a")
    t2 = store.create_task("b@d", "b@d", "Inbox", "/tmp/b")
    claimed = store.claim_next()
    assert claimed["id"] == t1
    assert store.get_task(t1)["status"] == "running"
    assert store.claim_next()["id"] == t2
    assert store.claim_next() is None


def test_progress_and_status_updates(tmp_path):
    store = TaskStore(str(tmp_path / "p.db"))
    tid = store.create_task("a@d", "a@d", "Inbox", "/tmp/a")
    store.set_totals(tid, 10)
    store.update_progress(tid, done=4, failed=1)
    store.set_failures(tid, [{"name": "x.eml", "reason": "bad"}])
    store.set_status(tid, "done")
    task = store.get_task(tid)
    assert task["total"] == 10 and task["done"] == 4 and task["failed"] == 1
    assert task["status"] == "done"
    import json
    assert json.loads(task["failures"])[0]["name"] == "x.eml"


def test_count_active_and_recover_interrupted(tmp_path):
    store = TaskStore(str(tmp_path / "r.db"))
    t1 = store.create_task("a@d", "a@d", "Inbox", "/tmp/a")
    store.create_task("b@d", "b@d", "Inbox", "/tmp/b")
    assert store.count_active() == 2
    store.claim_next()  # t1 -> running
    store.recover_interrupted()  # running -> interrupted
    assert store.get_task(t1)["status"] == "interrupted"


def test_purge_old_removes_aged_finished_tasks(tmp_path):
    import sqlite3
    store = TaskStore(str(tmp_path / "purge.db"))
    old = store.create_task("a@d", "a@d", "Inbox", "/tmp/old")
    store.set_status(old, "done")
    fresh = store.create_task("b@d", "b@d", "Inbox", "/tmp/fresh")
    store.set_status(fresh, "done")
    # backdate the old task's updated_at to 30 days ago
    conn = sqlite3.connect(str(tmp_path / "purge.db"))
    conn.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00' "
                 "WHERE id=?", (old,))
    conn.commit()
    conn.close()
    removed = store.purge_old(7)
    assert "/tmp/old" in removed
    assert store.get_task(old) is None
    assert store.get_task(fresh) is not None


def test_purge_old_does_not_delete_queued_or_running(tmp_path):
    """Safety guard: even a backdated queued/running task must survive purge,
    otherwise a stalled long-running import could be wiped mid-flight."""
    import sqlite3
    store = TaskStore(str(tmp_path / "purge_safe.db"))
    queued = store.create_task("q@d", "q@d", "Inbox", "/tmp/q")
    running = store.create_task("r@d", "r@d", "Inbox", "/tmp/r")
    store.set_status(running, "running")
    # Backdate both so the cutoff would otherwise catch them
    conn = sqlite3.connect(str(tmp_path / "purge_safe.db"))
    conn.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00'")
    conn.commit()
    conn.close()
    removed = store.purge_old(7)
    assert removed == []
    assert store.get_task(queued) is not None
    assert store.get_task(running) is not None
