from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import threading
from pathlib import Path
from typing import Any

import pytest

from xhbx_rag.atomic_indexer import (
    AtomicIndexError,
    AtomicIndexer,
    RollbackPendingError,
)
from xhbx_rag.index_lock import _normalize_uri, collection_write_lock


class FakeTransactionalStore:
    uri = "memory://atomic-indexer-tests"
    collection_name = "chunks"

    def __init__(
        self,
        *,
        existing: dict[str, dict[str, Any]] | None = None,
        collection_existed: bool = True,
        fail_after_upsert: bool = False,
        fail_delete: bool = False,
        fail_drop: bool = False,
        omit_upsert_ids: set[str] | None = None,
    ) -> None:
        self.rows = copy.deepcopy(existing or {})
        self.exists = collection_existed
        self.fail_next_flush = fail_after_upsert
        self.fail_delete = fail_delete
        self.delete_error = "delete failed"
        self.fail_drop = fail_drop
        self.omit_upsert_ids = omit_upsert_ids or set()
        self.upsert_calls = 0
        self.flush_calls = 0
        self.delete_calls: list[list[str]] = []
        self.collection_checks = 0
        self.ensure_calls: list[int] = []
        self.drop_calls = 0

    def collection_exists(self) -> bool:
        self.collection_checks += 1
        return self.exists

    def ensure_collection(self, vector_dim: int) -> None:
        assert vector_dim > 0
        self.ensure_calls.append(vector_dim)
        self.exists = True

    def drop_collection(self) -> None:
        self.drop_calls += 1
        if self.fail_drop:
            raise RuntimeError("drop failed")
        self.exists = False
        self.rows.clear()

    def fetch_raw_rows_by_ids(
        self, chunk_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not self.exists:
            return {}
        return {
            chunk_id: copy.deepcopy(self.rows[chunk_id])
            for chunk_id in chunk_ids
            if chunk_id in self.rows
        }

    def delete_by_ids(self, chunk_ids: list[str]) -> None:
        self.delete_calls.append(list(chunk_ids))
        if self.fail_delete:
            raise RuntimeError(self.delete_error)
        for chunk_id in chunk_ids:
            self.rows.pop(chunk_id, None)

    def upsert_raw_rows(self, rows: list[dict[str, Any]]) -> None:
        self.upsert_calls += 1
        for row in rows:
            if str(row["chunk_id"]) in self.omit_upsert_ids:
                continue
            self.rows[str(row["chunk_id"])] = copy.deepcopy(row)

    def flush(self) -> None:
        self.flush_calls += 1
        if self.fail_next_flush:
            self.fail_next_flush = False
            raise RuntimeError("flush failed")


class FakeEmbedding:
    def __init__(
        self,
        vectors: list[list[float]],
        *,
        error: Exception | None = None,
        called: threading.Event | None = None,
    ) -> None:
        self.vectors = vectors
        self.error = error
        self.called = called
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.called is not None:
            self.called.set()
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.vectors)


def chunk(chunk_id: str, text: str) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "chunk_type": "script",
        "text": text,
        "metadata": {
            "case_name": "案例A",
            "stage": "售前",
            "scenario": "客户抗拒",
        },
        "citations": [{"section_name": "第1节", "quote": f"引文-{chunk_id}"}],
        "source_file": "case.sales_insights.json",
    }


def raw_row(chunk_id: str, text: str, vector: list[float]) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "vector": list(vector),
        "text": text,
        "text_hash": f"hash-{chunk_id}-{text}",
        "case_name": "旧案例",
        "chunk_type": "script",
        "stage": "成交",
        "scenario": "旧场景",
        "metadata_json": json.dumps({"case_name": "旧案例"}, ensure_ascii=False),
        "citations_json": json.dumps(
            [{"section_name": "旧章节", "quote": "旧引文"}], ensure_ascii=False
        ),
    }


def write_chunks(path: Path, chunks: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in chunks),
        encoding="utf-8",
    )
    return path


def read_journal(journal_dir: Path) -> dict[str, Any]:
    return json.loads((journal_dir / "journal.json").read_text(encoding="utf-8"))


def write_prepared_journal(
    journal_dir: Path,
    *,
    store: FakeTransactionalStore,
    chunk_ids: list[str],
    old_rows: list[dict[str, Any]],
    collection_existed: bool,
    state: str = "prepared",
    snapshot_path: Path | None = None,
) -> Path:
    journal_dir.mkdir(parents=True)
    snapshot = snapshot_path or journal_dir / "snapshot.jsonl"
    snapshot.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in old_rows),
        encoding="utf-8",
    )
    journal_path = journal_dir / "journal.json"
    journal_path.write_text(
        json.dumps(
            {
                "version": 1,
                "collection": {
                    "uri_sha256": hashlib.sha256(
                        _normalize_uri(store.uri).encode("utf-8")
                    ).hexdigest(),
                    "collection_name": store.collection_name,
                },
                "collection_existed": collection_existed,
                "chunk_ids": sorted(chunk_ids),
                "snapshot_path": str(snapshot),
                "state": state,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return journal_path


def test_commit_failure_deletes_new_rows_and_restores_old_rows(tmp_path: Path) -> None:
    store = FakeTransactionalStore(
        existing={"same": raw_row("same", "旧文本", [0.1, 0.2])},
        fail_after_upsert=True,
    )
    indexer = AtomicIndexer(
        embedding_client=FakeEmbedding([[0.9, 0.8], [0.7, 0.6]]),
        store=store,
    )
    chunks = write_chunks(
        tmp_path / "chunks.jsonl",
        [chunk("same", "新文本"), chunk("new", "新增文本")],
    )

    with pytest.raises(Exception, match="flush failed"):
        indexer.commit(chunks, journal_dir=tmp_path / "rollback")

    assert store.rows["same"] == raw_row("same", "旧文本", [0.1, 0.2])
    assert "new" not in store.rows
    assert read_journal(tmp_path / "rollback")["state"] == "rolled_back"


def test_commit_success_persists_minimal_durable_journal_and_notifies_states(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})
    store.uri = "https://user:secret@example.test:19530/?token=do-not-persist"
    embedding = FakeEmbedding([[0.1, 0.2], [0.3, 0.4]])
    states: list[tuple[str, bool, bool]] = []
    journal_dir = tmp_path / "rollback"

    def on_state(state: str, journal_path: Path) -> None:
        states.append(
            (
                state,
                journal_path.is_file(),
                (journal_path.parent / "snapshot.jsonl").is_file(),
            )
        )

    result = AtomicIndexer(embedding_client=embedding, store=store).commit(
        write_chunks(
            tmp_path / "chunks.jsonl",
            [chunk("z", "敏感新文本-z"), chunk("a", "敏感新文本-a")],
        ),
        journal_dir=journal_dir,
        on_state=on_state,
    )

    assert (result.indexed, result.vector_dim) == (2, 2)
    assert embedding.calls == [["敏感新文本-z", "敏感新文本-a"]]
    assert store.upsert_calls == 1
    assert store.flush_calls == 1
    assert states == [("prepared", True, True), ("committed", True, True)]
    journal = read_journal(journal_dir)
    assert journal["chunk_ids"] == ["a", "z"]
    assert journal["snapshot_path"] == "snapshot.jsonl"
    assert journal["state"] == "committed"
    encoded = json.dumps(journal, ensure_ascii=False)
    assert "secret" not in encoded
    assert "do-not-persist" not in encoded
    assert "敏感新文本" not in encoded
    assert journal["collection"]["uri_sha256"] != hashlib.sha256(
        store.uri.encode("utf-8")
    ).hexdigest()
    assert stat.S_IMODE((journal_dir / "journal.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((journal_dir / "snapshot.jsonl").stat().st_mode) == 0o600
    assert not list(journal_dir.glob("*.tmp"))


def test_creating_journal_directory_fsyncs_its_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeTransactionalStore(existing={})
    journal_dir = tmp_path / "attempt" / "rollback"
    journal_dir.parent.mkdir()
    fsynced: list[tuple[int, int]] = []
    original_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        metadata = os.fstat(fd)
        fsynced.append((metadata.st_dev, metadata.st_ino))
        original_fsync(fd)

    monkeypatch.setattr("os.fsync", recording_fsync)

    AtomicIndexer(
        embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
    ).commit(
        write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
        journal_dir=journal_dir,
    )

    parent = journal_dir.parent.stat()
    assert (parent.st_dev, parent.st_ino) in fsynced


def test_embedding_failure_happens_before_any_store_access(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={})
    indexer = AtomicIndexer(
        embedding_client=FakeEmbedding([], error=RuntimeError("embedding failed")),
        store=store,
    )

    with pytest.raises(RuntimeError, match="embedding failed"):
        indexer.commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("one", "文本")]),
            journal_dir=tmp_path / "rollback",
        )

    assert store.collection_checks == 0
    assert store.upsert_calls == 0
    assert not (tmp_path / "rollback").exists()


def test_embedding_completes_while_collection_lock_is_held_elsewhere(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})
    store.uri = str(tmp_path / "fake.db")
    called = threading.Event()
    indexer = AtomicIndexer(
        embedding_client=FakeEmbedding([[0.1, 0.2]], called=called),
        store=store,
    )
    errors: list[BaseException] = []

    def commit() -> None:
        try:
            indexer.commit(
                write_chunks(tmp_path / "chunks.jsonl", [chunk("one", "文本")]),
                journal_dir=tmp_path / "rollback",
            )
        except BaseException as exc:  # pragma: no cover - 仅用于转交线程异常
            errors.append(exc)

    with collection_write_lock(store.uri, store.collection_name):
        worker = threading.Thread(target=commit)
        worker.start()
        assert called.wait(timeout=2), "embedding 不应等待 collection 写锁"
        assert worker.is_alive(), "embedding 后应等待当前持有者释放写锁"
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert errors == []


@pytest.mark.parametrize(
    ("vectors", "chunk_count", "message"),
    [
        ([], 1, "数量"),
        ([[0.1], [0.2]], 1, "数量"),
        ([[0.1], [0.2, 0.3]], 2, "维度"),
    ],
)
def test_invalid_embeddings_do_not_prepare_or_write(
    tmp_path: Path,
    vectors: list[list[float]],
    chunk_count: int,
    message: str,
) -> None:
    store = FakeTransactionalStore(existing={})

    with pytest.raises(AtomicIndexError, match=message):
        AtomicIndexer(embedding_client=FakeEmbedding(vectors), store=store).commit(
            write_chunks(
                tmp_path / "chunks.jsonl",
                [chunk(f"id-{index}", "文本") for index in range(chunk_count)],
            ),
            journal_dir=tmp_path / "rollback",
        )

    assert store.collection_checks == 0
    assert store.upsert_calls == 0


def test_failure_after_creating_new_collection_drops_it(tmp_path: Path) -> None:
    store = FakeTransactionalStore(
        existing={}, collection_existed=False, fail_after_upsert=True
    )

    with pytest.raises(RuntimeError, match="flush failed"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=tmp_path / "rollback",
        )

    assert store.exists is False
    assert store.rows == {}
    assert store.drop_calls == 1
    assert read_journal(tmp_path / "rollback")["state"] == "rolled_back"


def test_write_verification_failure_is_compensated(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={}, omit_upsert_ids={"missing"})

    with pytest.raises(AtomicIndexError, match="写后 ID 集合"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2], [0.3, 0.4]]), store=store
        ).commit(
            write_chunks(
                tmp_path / "chunks.jsonl",
                [chunk("kept", "保留"), chunk("missing", "丢失")],
            ),
            journal_dir=tmp_path / "rollback",
        )

    assert store.rows == {}
    assert read_journal(tmp_path / "rollback")["state"] == "rolled_back"


def test_journal_write_failure_never_upserts_and_removes_snapshot(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})
    journal_dir = tmp_path / "rollback"
    journal_dir.mkdir()
    (journal_dir / "journal.json").mkdir()

    with pytest.raises(OSError):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=journal_dir,
        )

    assert store.upsert_calls == 0
    assert not (journal_dir / "snapshot.jsonl").exists()


def test_journal_directory_fsync_failure_preserves_possible_recovery_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeTransactionalStore(
        existing={"same": raw_row("same", "旧文本", [0.1, 0.2])}
    )
    journal_dir = tmp_path / "rollback"
    journal_path = journal_dir / "journal.json"
    original_fsync = os.fsync
    failed = False

    def fail_after_journal_replace(fd: int) -> None:
        nonlocal failed
        metadata = os.fstat(fd)
        journal_dir_exists = journal_dir.exists()
        journal_dir_metadata = journal_dir.stat() if journal_dir_exists else None
        is_journal_directory = (
            journal_dir_metadata is not None
            and metadata.st_dev == journal_dir_metadata.st_dev
            and metadata.st_ino == journal_dir_metadata.st_ino
        )
        if not failed and is_journal_directory and journal_path.is_file():
            failed = True
            raise OSError("directory fsync failed")
        original_fsync(fd)

    monkeypatch.setattr("os.fsync", fail_after_journal_replace)

    with pytest.raises(OSError, match="directory fsync failed"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.9, 0.8]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("same", "新文本")]),
            journal_dir=journal_dir,
        )

    assert store.upsert_calls == 0
    assert journal_path.is_file()
    assert (journal_dir / "snapshot.jsonl").is_file()
    assert read_journal(journal_dir)["state"] == "prepared"


def test_prepared_callback_failure_is_compensated(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={})

    def on_state(state: str, _journal_path: Path) -> None:
        if state == "prepared":
            raise RuntimeError("state store failed")

    with pytest.raises(RuntimeError, match="state store failed"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=tmp_path / "rollback",
            on_state=on_state,
        )

    assert store.rows == {}
    assert read_journal(tmp_path / "rollback")["state"] == "rolled_back"


def test_prepared_callback_failure_cannot_block_physical_compensation(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})

    def unavailable_state_store(_state: str, _journal_path: Path) -> None:
        raise RuntimeError("state store unavailable")

    with pytest.raises(RuntimeError, match="state store unavailable"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=tmp_path / "rollback",
            on_state=unavailable_state_store,
        )

    assert store.rows == {}
    assert read_journal(tmp_path / "rollback")["state"] == "rolled_back"


def test_commit_raises_pending_when_physical_rollback_fails(tmp_path: Path) -> None:
    store = FakeTransactionalStore(
        existing={}, fail_after_upsert=True, fail_delete=True
    )
    journal_dir = tmp_path / "rollback"

    with pytest.raises(RollbackPendingError) as caught:
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=journal_dir,
        )

    assert caught.value.journal_path == journal_dir / "journal.json"
    assert "new" in store.rows
    assert read_journal(journal_dir)["state"] == "rolling_back"


def test_committed_callback_failure_does_not_roll_back(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={})

    def on_state(state: str, _journal_path: Path) -> None:
        if state == "committed":
            raise RuntimeError("sqlite state failed")

    with pytest.raises(RuntimeError, match="sqlite state failed"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=tmp_path / "rollback",
            on_state=on_state,
        )

    assert "new" in store.rows
    assert store.delete_calls == []
    assert read_journal(tmp_path / "rollback")["state"] == "committed"


def test_recover_prepared_journal_rolls_back(tmp_path: Path) -> None:
    store = FakeTransactionalStore(
        existing={
            "same": raw_row("same", "新文本", [0.9, 0.8]),
            "new": raw_row("new", "新增文本", [0.7, 0.6]),
        }
    )
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same", "new"],
        old_rows=[raw_row("same", "旧文本", [0.1, 0.2])],
        collection_existed=True,
    )

    AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.rows["same"] == raw_row("same", "旧文本", [0.1, 0.2])
    assert "new" not in store.rows
    assert read_journal(journal.parent)["state"] == "rolled_back"


def test_recover_rolling_back_is_idempotent(tmp_path: Path) -> None:
    old = raw_row("same", "旧文本", [0.1, 0.2])
    store = FakeTransactionalStore(existing={"same": old})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same", "new"],
        old_rows=[old],
        collection_existed=True,
        state="rolling_back",
    )
    indexer = AtomicIndexer(embedding_client=FakeEmbedding([]), store=store)

    indexer.recover(journal)
    indexer.recover(journal)

    assert store.rows == {"same": old}
    assert read_journal(journal.parent)["state"] == "rolled_back"


def test_rollback_failure_raises_pending_and_keeps_journal(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={}, fail_delete=True)
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
    )

    with pytest.raises(RollbackPendingError) as caught:
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert caught.value.journal_path == journal
    assert read_journal(journal.parent)["state"] == "rolling_back"


def test_rollback_pending_error_does_not_expose_backend_detail(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={}, fail_delete=True)
    store.delete_error = (
        "delete https://user:token@example.test failed at /private/secret/db"
    )
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
    )

    with pytest.raises(RollbackPendingError) as caught:
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert str(caught.value) == "知识库回滚尚未完成，请稍后重试"
    assert "token" not in str(caught.value)
    assert "/private" not in str(caught.value)


def test_recover_rejects_incomplete_snapshot_before_destructive_delete(
    tmp_path: Path,
) -> None:
    current = raw_row("same", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"same": current})
    incomplete = raw_row("same", "旧文本", [0.1, 0.2])
    incomplete.pop("vector")
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[incomplete],
        collection_existed=True,
    )

    with pytest.raises(AtomicIndexError, match="字段不完整"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.rows == {"same": current}
    assert store.delete_calls == []
    assert read_journal(journal.parent)["state"] == "prepared"


def test_recover_can_resume_after_rollback_failure(tmp_path: Path) -> None:
    store = FakeTransactionalStore(
        existing={"new": raw_row("new", "新增", [0.1, 0.2])}, fail_delete=True
    )
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
    )
    indexer = AtomicIndexer(embedding_client=FakeEmbedding([]), store=store)
    with pytest.raises(RollbackPendingError):
        indexer.recover(journal)

    store.fail_delete = False
    indexer.recover(journal)

    assert store.rows == {}
    assert read_journal(journal.parent)["state"] == "rolled_back"


def test_recover_drops_collection_that_did_not_exist_before(tmp_path: Path) -> None:
    store = FakeTransactionalStore(
        existing={"new": raw_row("new", "新增", [0.1, 0.2])},
        collection_existed=True,
    )
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=False,
    )

    AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.exists is False
    assert store.rows == {}
    assert read_journal(journal.parent)["state"] == "rolled_back"


def test_recover_committed_only_verifies_and_never_rolls_back(tmp_path: Path) -> None:
    committed = raw_row("new", "已提交", [0.1, 0.2])
    store = FakeTransactionalStore(existing={"new": committed})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
        state="committed",
    )

    AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.rows == {"new": committed}
    assert store.delete_calls == []
    assert read_journal(journal.parent)["state"] == "committed"


def test_recover_accepts_equivalent_uri_without_persisting_credentials(
    tmp_path: Path,
) -> None:
    committed = raw_row("new", "已提交", [0.1, 0.2])
    store = FakeTransactionalStore(existing={"new": committed})
    store.uri = "https://user:secret@example.test:19530/path/?token=old"
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
        state="committed",
    )
    store.uri = "https://example.test:19530/path"

    AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.rows == {"new": committed}
    assert read_journal(journal.parent)["state"] == "committed"


def test_recover_committed_missing_id_reports_error_without_rollback(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["missing"],
        old_rows=[],
        collection_existed=True,
        state="committed",
    )

    with pytest.raises(AtomicIndexError, match="committed.*ID"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.delete_calls == []
    assert read_journal(journal.parent)["state"] == "committed"


def test_recover_rejects_snapshot_outside_journal_directory(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={})
    outside = tmp_path / "secret.jsonl"
    outside.write_text(json.dumps(raw_row("same", "敏感原文", [0.1, 0.2])), encoding="utf-8")
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[],
        collection_existed=True,
        snapshot_path=outside,
    )

    with pytest.raises(AtomicIndexError, match="snapshot_path"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.delete_calls == []
