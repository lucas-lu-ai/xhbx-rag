from __future__ import annotations

import copy
import errno
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
    AtomicJournalIdentity,
    RollbackPendingError,
    UntrustedJournalError,
)
from xhbx_rag.chunk_io import chunk_text_hash
from xhbx_rag.index_lock import _normalize_uri, collection_write_lock


RAW_SCHEMA = sorted(
    {
        "chunk_id",
        "vector",
        "text",
        "text_hash",
        "case_name",
        "chunk_type",
        "stage",
        "scenario",
        "metadata_json",
        "citations_json",
    }
)


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
        self.fetch_calls = 0
        self.next_flush_error: BaseException | None = None
        self.wrong_ids_on_next_verify = False
        self.corrupt_fetch_field: str | None = None
        self.on_fetch: Any = None

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
        self.fetch_calls += 1
        if self.on_fetch is not None:
            callback = self.on_fetch
            self.on_fetch = None
            callback()
        if not self.exists:
            return {}
        rows = {
            chunk_id: copy.deepcopy(self.rows[chunk_id])
            for chunk_id in chunk_ids
            if chunk_id in self.rows
        }
        if self.wrong_ids_on_next_verify and self.upsert_calls == 1:
            self.wrong_ids_on_next_verify = False
            return {
                f"wrong-{index}": copy.deepcopy(row)
                for index, row in enumerate(rows.values())
            }
        if self.corrupt_fetch_field is not None and self.upsert_calls > 0:
            for row in rows.values():
                row.pop(self.corrupt_fetch_field, None)
        return rows

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
        if self.next_flush_error is not None:
            error = self.next_flush_error
            self.next_flush_error = None
            raise error
        if self.fail_next_flush:
            self.fail_next_flush = False
            raise RuntimeError("flush failed")


class FakeEmbedding:
    def __init__(
        self,
        vectors: list[list[float]],
        *,
        error: BaseException | None = None,
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
        "text_hash": chunk_text_hash(text),
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


def journal_with_checksum(data: dict[str, Any]) -> dict[str, Any]:
    core = {key: value for key, value in data.items() if key != "journal_sha256"}
    payload = json.dumps(
        core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {**core, "journal_sha256": hashlib.sha256(payload).hexdigest()}


def write_journal_data(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(
            journal_with_checksum(data),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def write_prepared_journal(
    journal_dir: Path,
    *,
    store: FakeTransactionalStore,
    chunk_ids: list[str],
    old_rows: list[dict[str, Any]],
    collection_existed: bool,
    state: str = "prepared",
    snapshot_path: Path | None = None,
    vector_dim: int = 2,
) -> Path:
    journal_dir.mkdir(parents=True)
    snapshot = snapshot_path or journal_dir / "snapshot.jsonl"
    snapshot_payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in old_rows
    ).encode("utf-8")
    snapshot.write_bytes(snapshot_payload)
    journal_path = journal_dir / "journal.json"
    write_journal_data(
        journal_path,
        {
            "version": 2,
            "owner": {
                "job_id": "standalone",
                "attempt_no": 1,
                "chunks_sha256": "0" * 64,
            },
            "collection": {
                "uri_sha256": hashlib.sha256(
                    _normalize_uri(store.uri).encode("utf-8")
                ).hexdigest(),
                "collection_name": store.collection_name,
            },
            "collection_existed": collection_existed,
            "chunk_ids": sorted(chunk_ids),
            "snapshot_path": str(snapshot),
            "snapshot_sha256": hashlib.sha256(snapshot_payload).hexdigest(),
            "snapshot_size": len(snapshot_payload),
            "snapshot_count": len(old_rows),
            "vector_dim": vector_dim,
            "raw_schema": RAW_SCHEMA,
            "state": state,
        },
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

    with pytest.raises(AtomicIndexError) as caught:
        indexer.commit(chunks, journal_dir=tmp_path / "rollback")

    assert str(caught.value) == "知识库写入失败，已完成回滚"
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "flush failed" not in str(caught.value)
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
    assert journal["snapshot_count"] == 0
    assert journal["snapshot_size"] == 0
    assert journal["snapshot_sha256"] == hashlib.sha256(b"").hexdigest()
    assert journal["vector_dim"] == 2
    assert journal["raw_schema"] == RAW_SCHEMA
    assert journal == journal_with_checksum(journal)
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


def test_commit_rejects_existing_recovery_material_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeTransactionalStore(existing={})
    journal_dir = tmp_path / "rollback"
    journal_path = write_prepared_journal(
        journal_dir,
        store=store,
        chunk_ids=["old"],
        old_rows=[],
        collection_existed=True,
    )
    snapshot_path = journal_dir / "snapshot.jsonl"
    original_pair = (journal_path.read_bytes(), snapshot_path.read_bytes())

    def unexpected_replace(_source: object, _target: object) -> None:
        raise AssertionError("已有恢复材料时不得尝试 replace")

    monkeypatch.setattr("os.replace", unexpected_replace)

    with pytest.raises(AtomicIndexError, match="回滚目录"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=journal_dir,
        )

    assert (journal_path.read_bytes(), snapshot_path.read_bytes()) == original_pair
    assert store.upsert_calls == 0
    assert store.delete_calls == []
    assert store.drop_calls == 0


def test_commit_rechecks_empty_directory_immediately_before_snapshot(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})
    journal_dir = tmp_path / "rollback"

    def create_conflicting_material() -> None:
        journal_dir.mkdir(parents=True, exist_ok=True)
        (journal_dir / "conflict").write_text("do not overwrite", encoding="utf-8")

    store.on_fetch = create_conflicting_material

    with pytest.raises(AtomicIndexError, match="提交准备"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=journal_dir,
        )

    assert (journal_dir / "conflict").read_text(encoding="utf-8") == "do not overwrite"
    assert not (journal_dir / "snapshot.jsonl").exists()
    assert not (journal_dir / "journal.json").exists()
    assert store.upsert_calls == 0


def test_commit_never_overwrites_snapshot_created_after_final_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeTransactionalStore(existing={})
    journal_dir = tmp_path / "rollback"
    snapshot_path = journal_dir / "snapshot.jsonl"
    foreign_snapshot = b"foreign recovery evidence\n"
    original_link = os.link
    injected = False

    def race_snapshot_publish(source: object, target: object) -> None:
        nonlocal injected
        target_path = Path(target)
        if not injected and target_path.name == "snapshot.jsonl":
            injected = True
            target_path.write_bytes(foreign_snapshot)
        original_link(source, target)

    monkeypatch.setattr("os.link", race_snapshot_publish)

    with pytest.raises(AtomicIndexError, match="提交准备"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=journal_dir,
        )

    assert snapshot_path.read_bytes() == foreign_snapshot
    assert not (journal_dir / "journal.json").exists()
    assert store.upsert_calls == 0


def test_second_file_publish_failure_never_deletes_foreign_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeTransactionalStore(existing={})
    journal_dir = tmp_path / "rollback"
    snapshot_path = journal_dir / "snapshot.jsonl"
    journal_path = journal_dir / "journal.json"
    foreign_snapshot = b"foreign snapshot\n"
    foreign_journal = b'{"foreign":true}\n'
    original_link = os.link
    injected = False

    def race_journal_publish(source: object, target: object) -> None:
        nonlocal injected
        target_path = Path(target)
        if not injected and target_path.name == "journal.json":
            injected = True
            snapshot_path.unlink()
            snapshot_path.write_bytes(foreign_snapshot)
            journal_path.write_bytes(foreign_journal)
        original_link(source, target)

    monkeypatch.setattr("os.link", race_journal_publish)

    with pytest.raises(AtomicIndexError, match="提交准备"):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=journal_dir,
        )

    assert snapshot_path.read_bytes() == foreign_snapshot
    assert journal_path.read_bytes() == foreign_journal
    assert store.upsert_calls == 0


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

    with pytest.raises(AtomicIndexError) as caught:
        indexer.commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("one", "文本")]),
            journal_dir=tmp_path / "rollback",
        )

    assert str(caught.value) == "向量生成失败"
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "embedding failed" not in str(caught.value)
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


@pytest.mark.parametrize(
    "vector",
    [
        [True, 0.2],
        ["0.1", 0.2],
        [float("nan"), 0.2],
        [float("inf"), 0.2],
        [float("-inf"), 0.2],
        [10**10000, 0.2],
    ],
)
def test_invalid_embedding_elements_are_rejected_before_store_access(
    tmp_path: Path, vector: list[Any]
) -> None:
    store = FakeTransactionalStore(existing={})

    with pytest.raises(AtomicIndexError, match="数值"):
        AtomicIndexer(embedding_client=FakeEmbedding([vector]), store=store).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("one", "文本")]),
            journal_dir=tmp_path / "rollback",
        )

    assert store.collection_checks == 0
    assert store.upsert_calls == 0
    assert not (tmp_path / "rollback").exists()


def test_empty_chunks_are_rejected_without_embedding_or_store_access(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})
    embedding = FakeEmbedding([])

    with pytest.raises(AtomicIndexError, match="不能为空"):
        AtomicIndexer(embedding_client=embedding, store=store).commit(
            write_chunks(tmp_path / "chunks.jsonl", []),
            journal_dir=tmp_path / "rollback",
        )

    assert embedding.calls == []
    assert store.collection_checks == 0


def test_duplicate_chunk_ids_are_rejected_before_embedding_and_store_access(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})
    embedding = FakeEmbedding([[0.1, 0.2], [0.3, 0.4]])

    with pytest.raises(AtomicIndexError, match="重复"):
        AtomicIndexer(embedding_client=embedding, store=store).commit(
            write_chunks(
                tmp_path / "chunks.jsonl",
                [chunk("same", "文本一"), chunk("same", "文本二")],
            ),
            journal_dir=tmp_path / "rollback",
        )

    assert embedding.calls == []
    assert store.collection_checks == 0


def test_failure_after_creating_new_collection_drops_it(tmp_path: Path) -> None:
    store = FakeTransactionalStore(
        existing={}, collection_existed=False, fail_after_upsert=True
    )

    with pytest.raises(AtomicIndexError) as caught:
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=tmp_path / "rollback",
        )

    assert str(caught.value) == "知识库写入失败，已完成回滚"
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


def test_write_verification_rejects_same_count_with_wrong_ids(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={})
    store.wrong_ids_on_next_verify = True

    with pytest.raises(AtomicIndexError):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2], [0.3, 0.4]]), store=store
        ).commit(
            write_chunks(
                tmp_path / "chunks.jsonl",
                [chunk("one", "文本一"), chunk("two", "文本二")],
            ),
            journal_dir=tmp_path / "rollback",
        )

    assert store.rows == {}
    assert read_journal(tmp_path / "rollback")["state"] == "rolled_back"


@pytest.mark.parametrize("crash", [KeyboardInterrupt(), SystemExit(2)])
def test_base_exception_leaves_prepared_for_crash_recovery(
    tmp_path: Path, crash: BaseException
) -> None:
    store = FakeTransactionalStore(existing={})
    store.next_flush_error = crash
    journal_dir = tmp_path / "rollback"
    indexer = AtomicIndexer(
        embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
    )

    with pytest.raises(type(crash)):
        indexer.commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=journal_dir,
        )

    assert "new" in store.rows
    assert store.delete_calls == []
    assert read_journal(journal_dir)["state"] == "prepared"

    indexer.recover(journal_dir / "journal.json")
    assert store.rows == {}
    assert read_journal(journal_dir)["state"] == "rolled_back"


def test_committed_journal_write_failure_is_compensated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeTransactionalStore(existing={})
    original_replace = os.replace
    failed = False

    def fail_committed_replace(source: object, target: object) -> None:
        nonlocal failed
        source_path = Path(source)
        target_path = Path(target)
        if (
            not failed
            and target_path.name == "journal.json"
            and json.loads(source_path.read_text(encoding="utf-8"))["state"]
            == "committed"
        ):
            failed = True
            raise OSError("commit journal failed at /private/secret")
        original_replace(source, target)

    monkeypatch.setattr("os.replace", fail_committed_replace)

    with pytest.raises(AtomicIndexError):
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=tmp_path / "rollback",
        )

    assert store.rows == {}
    assert read_journal(tmp_path / "rollback")["state"] == "rolled_back"


def test_journal_publish_failure_never_upserts_or_overwrites_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeTransactionalStore(existing={})
    journal_dir = tmp_path / "rollback"
    journal_dir.mkdir()
    original_link = os.link
    original_fsync = os.fsync
    directory_fsync_temp_states: list[bool] = []

    def fail_journal_publish(source: object, target: object) -> None:
        if Path(target).name == "journal.json":
            raise OSError("journal publish failed at /private/secret")
        original_link(source, target)

    def record_directory_fsync(fd: int) -> None:
        metadata = os.fstat(fd)
        directory = journal_dir.stat()
        if (metadata.st_dev, metadata.st_ino) == (directory.st_dev, directory.st_ino):
            directory_fsync_temp_states.append(bool(list(journal_dir.glob(".*.tmp"))))
        original_fsync(fd)

    monkeypatch.setattr("os.link", fail_journal_publish)
    monkeypatch.setattr("os.fsync", record_directory_fsync)

    with pytest.raises(AtomicIndexError) as caught:
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=journal_dir,
        )

    assert str(caught.value) == "知识库提交准备失败"
    assert isinstance(caught.value.__cause__, OSError)
    assert "/private" not in str(caught.value)
    assert store.upsert_calls == 0
    assert (journal_dir / "snapshot.jsonl").is_file()
    assert not (journal_dir / "journal.json").exists()
    assert directory_fsync_temp_states[-1] is False


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

    with pytest.raises(AtomicIndexError) as caught:
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.9, 0.8]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("same", "新文本")]),
            journal_dir=journal_dir,
        )

    assert str(caught.value) == "知识库提交准备失败"
    assert isinstance(caught.value.__cause__, OSError)
    assert store.upsert_calls == 0
    assert journal_path.is_file()
    assert (journal_dir / "snapshot.jsonl").is_file()
    assert read_journal(journal_dir)["state"] == "prepared"


def test_prepared_callback_failure_is_compensated(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={})

    def on_state(state: str, _journal_path: Path) -> None:
        if state == "prepared":
            raise RuntimeError("state store failed")

    with pytest.raises(AtomicIndexError) as caught:
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=tmp_path / "rollback",
            on_state=on_state,
        )

    assert str(caught.value) == "知识库写入失败，已完成回滚"
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert store.rows == {}
    assert read_journal(tmp_path / "rollback")["state"] == "rolled_back"


def test_prepared_callback_failure_cannot_block_physical_compensation(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})

    def unavailable_state_store(_state: str, _journal_path: Path) -> None:
        raise RuntimeError("state store unavailable")

    with pytest.raises(AtomicIndexError) as caught:
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=tmp_path / "rollback",
            on_state=unavailable_state_store,
        )

    assert str(caught.value) == "知识库写入失败，已完成回滚"
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
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert str(caught.value.__cause__) == "delete failed"


def test_committed_callback_failure_does_not_roll_back(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={})

    def on_state(state: str, _journal_path: Path) -> None:
        if state == "committed":
            raise RuntimeError("sqlite state failed")

    with pytest.raises(AtomicIndexError) as caught:
        AtomicIndexer(
            embedding_client=FakeEmbedding([[0.1, 0.2]]), store=store
        ).commit(
            write_chunks(tmp_path / "chunks.jsonl", [chunk("new", "新增")]),
            journal_dir=tmp_path / "rollback",
            on_state=on_state,
        )

    assert str(caught.value) == "知识库已提交，但状态同步失败"
    assert isinstance(caught.value.__cause__, RuntimeError)
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


def test_inspect_journal_state_validates_prepared_snapshot_without_writing(
    tmp_path: Path,
) -> None:
    old = raw_row("same", "旧文本", [0.1, 0.2])
    store = FakeTransactionalStore(existing={"same": raw_row("same", "新文本", [0.9, 0.8])})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[old],
        collection_existed=True,
    )
    indexer = AtomicIndexer(embedding_client=FakeEmbedding([]), store=store)

    assert indexer.inspect_journal_state(journal) == "prepared"
    assert store.delete_calls == []
    assert store.upsert_calls == 0
    assert store.flush_calls == 0

    (journal.parent / "snapshot.jsonl").write_text("损坏快照\n", encoding="utf-8")
    with pytest.raises(AtomicIndexError, match="snapshot"):
        indexer.inspect_journal_state(journal)


def test_inspect_committed_journal_verifies_durable_ids(tmp_path: Path) -> None:
    current = raw_row("new", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"new": current})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
        state="committed",
    )
    indexer = AtomicIndexer(embedding_client=FakeEmbedding([]), store=store)

    assert indexer.inspect_journal_state(journal) == "committed"
    store.rows.clear()
    with pytest.raises(AtomicIndexError, match="ID 集合"):
        indexer.inspect_journal_state(journal)


def test_inspect_journal_state_wraps_store_errors_with_safe_detail(tmp_path: Path) -> None:
    current = raw_row("new", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"new": current})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
        state="committed",
    )

    def fail_fetch(chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        del chunk_ids
        raise RuntimeError("raw /private/path token=secret")

    store.fetch_raw_rows_by_ids = fail_fetch  # type: ignore[method-assign]

    with pytest.raises(AtomicIndexError) as caught:
        AtomicIndexer(
            embedding_client=FakeEmbedding([]), store=store
        ).inspect_journal_state(journal)

    assert str(caught.value) == "commit journal 状态检查失败"
    assert "/private/path" not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_expected_owner_mismatch_is_rejected_before_any_store_write(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={"new": raw_row("new", "新文本", [0.1, 0.2])})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
        state="committed",
    )
    wrong = AtomicJournalIdentity("other-job", 9, "f" * 64)
    indexer = AtomicIndexer(embedding_client=FakeEmbedding([]), store=store)

    with pytest.raises(AtomicIndexError, match="owner identity 不匹配"):
        indexer.inspect_journal_state(journal, expected_identity=wrong)
    with pytest.raises(AtomicIndexError, match="owner identity 不匹配"):
        indexer.recover(journal, expected_identity=wrong)

    assert store.delete_calls == []
    assert store.upsert_calls == 0
    assert store.flush_calls == 0


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink"])
def test_snapshot_local_type_failures_are_untrusted(
    tmp_path: Path, kind: str
) -> None:
    store = FakeTransactionalStore(existing={})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
    )
    snapshot = journal.parent / "snapshot.jsonl"
    snapshot.unlink()
    if kind == "directory":
        snapshot.mkdir()
    elif kind == "symlink":
        target = tmp_path / "foreign.snapshot"
        target.write_bytes(b"")
        snapshot.symlink_to(target)

    with pytest.raises(UntrustedJournalError):
        AtomicIndexer(
            embedding_client=FakeEmbedding([]), store=store
        ).inspect_journal_state(journal)

    assert store.delete_calls == []
    assert store.upsert_calls == 0


def test_snapshot_lstat_eio_remains_transient_atomic_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeTransactionalStore(existing={})
    journal = write_prepared_journal(
        tmp_path / "rollback", store=store, chunk_ids=["new"], old_rows=[],
        collection_existed=True,
    )
    snapshot = journal.parent / "snapshot.jsonl"
    real_lstat = Path.lstat

    def fail_lstat(path: Path):
        if path == snapshot:
            raise OSError(errno.EIO, "temporary storage error")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_lstat)
    with pytest.raises(AtomicIndexError) as caught:
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).inspect_journal_state(journal)
    assert not isinstance(caught.value, UntrustedJournalError)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029"])
def test_recover_preserves_unicode_line_separator_inside_snapshot_text(
    tmp_path: Path, separator: str
) -> None:
    old = raw_row("same", f"旧{separator}文本", [0.1, 0.2])
    current = raw_row("same", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"same": current})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[old],
        collection_existed=True,
    )

    AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.rows == {"same": old}
    assert read_journal(journal.parent)["state"] == "rolled_back"


@pytest.mark.parametrize("identity_change", ["uri", "collection_name"])
def test_recover_rejects_collection_identity_mismatch_without_store_writes(
    tmp_path: Path, identity_change: str
) -> None:
    store = FakeTransactionalStore(existing={})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
    )
    if identity_change == "uri":
        store.uri = "memory://different-store"
    else:
        store.collection_name = "other_chunks"

    with pytest.raises(AtomicIndexError, match="collection"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.upsert_calls == 0
    assert store.delete_calls == []
    assert store.drop_calls == 0
    assert store.flush_calls == 0


def test_recover_rolled_back_performs_no_store_operation(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
        state="rolled_back",
    )

    AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.collection_checks == 0
    assert store.fetch_calls == 0
    assert store.upsert_calls == 0
    assert store.delete_calls == []
    assert store.drop_calls == 0
    assert store.flush_calls == 0


def test_recover_rejects_journal_self_checksum_mismatch_before_store_write(
    tmp_path: Path,
) -> None:
    old = raw_row("same", "旧文本", [0.1, 0.2])
    current = raw_row("same", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"same": current})
    journal_path = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[old],
        collection_existed=True,
    )
    journal = read_journal(journal_path.parent)
    journal["collection_existed"] = False
    journal_path.write_text(json.dumps(journal, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AtomicIndexError, match="journal 自校验"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(
            journal_path
        )

    assert store.rows == {"same": current}
    assert store.delete_calls == []
    assert store.upsert_calls == 0
    assert store.drop_calls == 0


def test_recover_rejects_snapshot_digest_mismatch_before_store_write(
    tmp_path: Path,
) -> None:
    old = raw_row("same", "旧文本", [0.1, 0.2])
    current = raw_row("same", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"same": current})
    journal_path = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[old],
        collection_existed=True,
    )
    with (journal_path.parent / "snapshot.jsonl").open("ab") as handle:
        handle.write(b"\n")

    with pytest.raises(AtomicIndexError, match="snapshot 完整性"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(
            journal_path
        )

    assert store.rows == {"same": current}
    assert store.delete_calls == []
    assert store.upsert_calls == 0


@pytest.mark.parametrize(
    "chunk_ids",
    [[], ["two", "one"], ["one", "one"], ["one", 2]],
)
def test_recover_rejects_invalid_journal_ids_before_store_write(
    tmp_path: Path, chunk_ids: list[Any]
) -> None:
    store = FakeTransactionalStore(existing={})
    journal_path = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["one"],
        old_rows=[],
        collection_existed=True,
    )
    journal = read_journal(journal_path.parent)
    journal["chunk_ids"] = chunk_ids
    write_journal_data(journal_path, journal)

    with pytest.raises(AtomicIndexError, match="chunk_ids"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(
            journal_path
        )

    assert store.delete_calls == []
    assert store.upsert_calls == 0
    assert store.drop_calls == 0


def test_recover_rejects_boolean_journal_version_before_store_write(
    tmp_path: Path,
) -> None:
    store = FakeTransactionalStore(existing={})
    journal_path = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["one"],
        old_rows=[],
        collection_existed=True,
    )
    journal = read_journal(journal_path.parent)
    journal["version"] = True
    write_journal_data(journal_path, journal)

    with pytest.raises(AtomicIndexError, match="版本"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(
            journal_path
        )

    assert store.delete_calls == []
    assert store.upsert_calls == 0
    assert store.drop_calls == 0


def test_recover_rejects_snapshot_count_mismatch_before_store_write(
    tmp_path: Path,
) -> None:
    current = raw_row("same", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"same": current})
    journal_path = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[raw_row("same", "旧文本", [0.1, 0.2])],
        collection_existed=True,
    )
    journal = read_journal(journal_path.parent)
    journal["snapshot_count"] = 2
    write_journal_data(journal_path, journal)

    with pytest.raises(AtomicIndexError, match="行数"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(
            journal_path
        )

    assert store.rows == {"same": current}
    assert store.delete_calls == []
    assert store.upsert_calls == 0


def test_recover_rejects_blank_snapshot_line_before_store_write(
    tmp_path: Path,
) -> None:
    current = raw_row("same", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"same": current})
    journal_path = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[raw_row("same", "旧文本", [0.1, 0.2])],
        collection_existed=True,
    )
    snapshot_path = journal_path.parent / "snapshot.jsonl"
    payload = snapshot_path.read_bytes() + b"\n"
    snapshot_path.write_bytes(payload)
    journal = read_journal(journal_path.parent)
    journal["snapshot_sha256"] = hashlib.sha256(payload).hexdigest()
    journal["snapshot_size"] = len(payload)
    write_journal_data(journal_path, journal)

    with pytest.raises(AtomicIndexError, match="空行"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(
            journal_path
        )

    assert store.rows == {"same": current}
    assert store.delete_calls == []
    assert store.upsert_calls == 0


@pytest.mark.parametrize("corruption", ["vector_dim", "extra_field", "text_hash"])
def test_recover_rejects_semantically_invalid_snapshot_before_store_write(
    tmp_path: Path, corruption: str
) -> None:
    old = raw_row("same", "旧文本", [0.1, 0.2])
    current = raw_row("same", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"same": current})
    journal_path = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[old],
        collection_existed=True,
    )
    damaged = copy.deepcopy(old)
    if corruption == "vector_dim":
        damaged["vector"] = [0.1, 0.2, 0.3]
    elif corruption == "extra_field":
        damaged["unexpected"] = "not in Milvus schema"
    else:
        damaged["text_hash"] = "0" * 64
    snapshot_payload = (
        json.dumps(damaged, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    (journal_path.parent / "snapshot.jsonl").write_bytes(snapshot_payload)
    journal = read_journal(journal_path.parent)
    journal.update(
        snapshot_sha256=hashlib.sha256(snapshot_payload).hexdigest(),
        snapshot_size=len(snapshot_payload),
        snapshot_count=1,
    )
    write_journal_data(journal_path, journal)

    with pytest.raises(AtomicIndexError, match="snapshot"):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(
            journal_path
        )

    assert store.rows == {"same": current}
    assert store.delete_calls == []
    assert store.upsert_calls == 0


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

    with pytest.raises(AtomicIndexError, match="字段集合"):
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


def test_rollback_flushes_and_corrupt_restoration_remains_pending(
    tmp_path: Path,
) -> None:
    old = raw_row("same", "旧文本", [0.1, 0.2])
    current = raw_row("same", "新文本", [0.9, 0.8])
    store = FakeTransactionalStore(existing={"same": current})
    store.corrupt_fetch_field = "text_hash"
    journal = write_prepared_journal(
        tmp_path / "rollback",
        store=store,
        chunk_ids=["same"],
        old_rows=[old],
        collection_existed=True,
    )

    with pytest.raises(RollbackPendingError):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)

    assert store.flush_calls == 1
    assert store.delete_calls == [["same"]]
    assert read_journal(journal.parent)["state"] == "rolling_back"


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
