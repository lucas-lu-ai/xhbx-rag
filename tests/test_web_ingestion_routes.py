from __future__ import annotations

import asyncio
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import FormData, UploadFile as StarletteUploadFile

import xhbx_rag.web.ingestion_routes as ingestion_routes
from xhbx_rag.web.app import create_app
from xhbx_rag.web.ingestion_store import IngestionStore
from xhbx_rag.web.ingestion_uploads import IngestionLimits


class FakeRunner:
    def __init__(self, store: IngestionStore) -> None:
        self.store = store
        self.enqueued: list[str] = []
        self.status_at_enqueue: list[str] = []
        self.lifecycle: list[str] = []

    def enqueue(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        assert job is not None
        self.status_at_enqueue.append(str(job["status"]))
        self.enqueued.append(job_id)

    def recover_after_restart(self) -> None:
        self.lifecycle.append("recover")

    def start(self) -> None:
        self.lifecycle.append("start")

    def stop(self) -> None:
        self.lifecycle.append("stop")


def make_client(tmp_path: Path) -> tuple[TestClient, IngestionStore, FakeRunner]:
    jobs_root = tmp_path / "jobs"
    store = IngestionStore(tmp_path / "ingestion.sqlite3", jobs_root=jobs_root)
    runner = FakeRunner(store)
    app = create_app(ingestion_store=store, ingestion_runner=runner)
    app.state.ingestion_limits = IngestionLimits()
    return TestClient(app), store, runner


def create_draft(
    client: TestClient,
    *,
    filename: str = "course.txt",
    content: bytes = "课程内容".encode(),
    target: str = "course",
) -> str:
    response = client.post(
        "/api/ingestion-jobs",
        data={"target": target},
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["job_id"])


class FaultyUpload:
    def __init__(
        self,
        *,
        read_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.filename = "course.txt"
        self.read_error = read_error
        self.close_error = close_error
        self.read_count = 0
        self.closed = False

    async def read(self, _: int) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        self.read_count += 1
        return b"course" if self.read_count == 1 else b""

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class DirectRequest:
    def __init__(self, store: IngestionStore, runner: FakeRunner, upload: FaultyUpload) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                ingestion_store=store,
                ingestion_runner=runner,
                ingestion_limits=IngestionLimits(),
            )
        )
        self._form = FormData([("file", upload), ("target", "course")])

    async def form(self) -> FormData:
        return self._form


def invoke_direct_create(
    store: IngestionStore, runner: FakeRunner, upload: FaultyUpload
) -> dict[str, object]:
    request = DirectRequest(store, runner, upload)
    return asyncio.run(
        ingestion_routes.create_ingestion_job(request, upload, "course")
    )


def fail_job(store: IngestionStore, job_id: str) -> Path:
    assert store.start_job(job_id) == "ok"
    assert store.claim_job(job_id) is True
    workspace = store.jobs_root / job_id / "attempts" / "1"
    workspace.mkdir(parents=True)
    store.begin_attempt(job_id, workspace)
    marker = workspace / "old-output.txt"
    marker.write_text("old", encoding="utf-8")
    store.fail_job(job_id, code="parse_failed", detail="raw parse error")
    return marker


def test_create_ingestion_job_returns_draft_preflight(tmp_path: Path) -> None:
    client, store, runner = make_client(tmp_path)

    response = client.post(
        "/api/ingestion-jobs",
        data={"target": "course"},
        files={"file": ("course.txt", "课程内容".encode(), "text/plain")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["target"] == "course"
    assert body["item_total"] == 1
    assert body["document_total"] == 1
    assert "source_path" not in body
    assert store.get_job(body["job_id"])["source_name"] == "course.txt"
    assert runner.enqueued == []


def test_create_uses_uuid_workspace_and_never_trusts_client_path(
    tmp_path: Path,
) -> None:
    client, store, _ = make_client(tmp_path)

    response = client.post(
        "/api/ingestion-jobs",
        data={"target": "course"},
        files={"file": (r"..\..\private/course.txt", b"course", "text/plain")},
    )

    assert response.status_code == 201
    job_id = response.json()["job_id"]
    assert len(job_id) == 32 and all(character in "0123456789abcdef" for character in job_id)
    execution = store.get_job_for_execution(job_id)
    assert execution is not None
    source_path = execution["source_path"]
    assert source_path == store.jobs_root / job_id / "source" / "course.txt"
    assert source_path.read_bytes() == b"course"
    assert not any(path.is_symlink() for path in (store.jobs_root, source_path.parent, source_path))


def test_create_rejects_preexisting_job_symlink_without_writing_through_it(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, _ = make_client(tmp_path)
    store.jobs_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    fixed_job_id = "a" * 32
    (store.jobs_root / fixed_job_id).symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        ingestion_routes.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=fixed_job_id),
    )

    response = client.post(
        "/api/ingestion-jobs",
        data={"target": "course"},
        files={"file": ("course.txt", b"course", "text/plain")},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "任务存储暂时不可用"}
    assert list(outside.iterdir()) == []
    assert store.list_jobs() == []


def test_create_failure_removes_workspace_and_committed_draft(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, _ = make_client(tmp_path)
    original_create = store.create_draft

    def create_then_fail(**kwargs):
        original_create(**kwargs)
        raise sqlite3.OperationalError("database path=/private/db token=secret")

    monkeypatch.setattr(store, "create_draft", create_then_fail)

    response = client.post(
        "/api/ingestion-jobs",
        data={"target": "course"},
        files={"file": ("course.txt", b"course", "text/plain")},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "任务存储暂时不可用"}
    assert store.list_jobs() == []
    assert list(store.jobs_root.iterdir()) == []


def test_upload_read_oserror_closes_and_rolls_back_all_state(tmp_path: Path) -> None:
    _, store, runner = make_client(tmp_path)
    upload = FaultyUpload(read_error=OSError("read /private/source token=secret"))

    with pytest.raises(HTTPException) as caught:
        invoke_direct_create(store, runner, upload)

    assert caught.value.status_code == 500
    assert caught.value.detail == "任务存储暂时不可用"
    assert upload.closed is True
    assert store.list_jobs() == []
    assert list(store.jobs_root.iterdir()) == []


def test_upload_close_error_after_draft_rolls_back_database_and_workspace(
    tmp_path: Path,
) -> None:
    _, store, runner = make_client(tmp_path)
    upload = FaultyUpload(close_error=OSError("close /private/source token=secret"))

    with pytest.raises(HTTPException) as caught:
        invoke_direct_create(store, runner, upload)

    assert caught.value.status_code == 500
    assert caught.value.detail == "任务存储暂时不可用"
    assert store.list_jobs() == []
    assert list(store.jobs_root.iterdir()) == []


@pytest.mark.parametrize("phase", ["read", "close"])
def test_upload_cancellation_before_or_after_draft_reraises_and_leaves_no_state(
    tmp_path: Path, phase: str
) -> None:
    _, store, runner = make_client(tmp_path)
    upload = FaultyUpload(
        read_error=asyncio.CancelledError() if phase == "read" else None,
        close_error=asyncio.CancelledError() if phase == "close" else None,
    )

    with pytest.raises(asyncio.CancelledError):
        invoke_direct_create(store, runner, upload)

    assert upload.closed is True
    assert store.list_jobs() == []
    assert list(store.jobs_root.iterdir()) == []


def test_outer_cancellation_waits_for_shielded_close_before_rollback(
    tmp_path: Path,
) -> None:
    _, store, runner = make_client(tmp_path)

    class CancellingCloseUpload(FaultyUpload):
        def __init__(self) -> None:
            super().__init__()
            self.owner: asyncio.Task | None = None
            self.close_finished = False

        async def read(self, size: int) -> bytes:
            self.owner = asyncio.current_task()
            return await super().read(size)

        async def close(self) -> None:
            self.closed = True
            assert self.owner is not None
            self.owner.cancel()
            await asyncio.sleep(0.05)
            self.close_finished = True

    upload = CancellingCloseUpload()

    with pytest.raises(asyncio.CancelledError):
        invoke_direct_create(store, runner, upload)

    assert upload.close_finished is True
    assert store.list_jobs() == []
    assert list(store.jobs_root.iterdir()) == []


def test_create_cleanup_failure_keeps_deleting_recovery_state_and_safe_error(
    tmp_path: Path, monkeypatch
) -> None:
    _, store, runner = make_client(tmp_path)
    upload = FaultyUpload(close_error=OSError("close /private/source token=secret"))

    def fail_delete(_: Path) -> None:
        raise OSError("delete /private/job token=secret")

    monkeypatch.setattr(ingestion_routes, "delete_job_workspace", fail_delete)

    with pytest.raises(HTTPException) as caught:
        invoke_direct_create(store, runner, upload)

    assert caught.value.status_code == 500
    assert caught.value.detail == "任务存储暂时不可用"
    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "deleting"
    assert (store.jobs_root / jobs[0]["job_id"]).is_dir()
    assert "private" not in str(caught.value.detail)
    assert "secret" not in str(caught.value.detail)


def test_duplicate_file_parts_are_rejected_and_all_uploads_closed(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, _ = make_client(tmp_path)
    closed: list[str] = []
    original_close = StarletteUploadFile.close

    async def recording_close(upload: StarletteUploadFile) -> None:
        closed.append(str(upload.filename))
        await original_close(upload)

    monkeypatch.setattr(StarletteUploadFile, "close", recording_close)

    response = client.post(
        "/api/ingestion-jobs",
        data={"target": "course"},
        files=[
            ("file", ("first.txt", b"first", "text/plain")),
            ("file", ("second.txt", b"second", "text/plain")),
        ],
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "上传文件无效"}
    assert {"first.txt", "second.txt"}.issubset(closed)
    assert store.list_jobs() == []
    assert not store.jobs_root.exists() or list(store.jobs_root.iterdir()) == []


def test_create_maps_only_stream_byte_overflow_to_413(tmp_path: Path) -> None:
    client, store, _ = make_client(tmp_path)
    client.app.state.ingestion_limits = IngestionLimits(max_upload_bytes=4)

    response = client.post(
        "/api/ingestion-jobs",
        data={"target": "course"},
        files={"file": ("course.txt", b"12345", "text/plain")},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "上传文件超过大小限制"}
    assert store.list_jobs() == []
    assert list(store.jobs_root.iterdir()) == []


def test_create_maps_zip_structure_and_extension_errors_to_400(tmp_path: Path) -> None:
    client, store, _ = make_client(tmp_path)
    zip_path = tmp_path / "oversized-entry.zip"
    with ZipFile(zip_path, "w") as archive:
        archive.writestr("course.txt", b"12345")
    client.app.state.ingestion_limits = IngestionLimits(max_entry_bytes=4)

    zip_response = client.post(
        "/api/ingestion-jobs",
        data={"target": "course"},
        files={"file": ("course.zip", zip_path.read_bytes(), "application/zip")},
    )
    extension_response = client.post(
        "/api/ingestion-jobs",
        data={"target": "course"},
        files={"file": ("course.exe", b"binary", "application/octet-stream")},
    )

    assert zip_response.status_code == 400
    assert zip_response.json() == {"detail": "上传文件无效"}
    assert extension_response.status_code == 400
    assert extension_response.json() == {"detail": "上传文件无效"}
    assert store.list_jobs() == []


def test_start_commits_before_enqueue(tmp_path: Path) -> None:
    client, store, runner = make_client(tmp_path)
    job_id = create_draft(client)

    response = client.post(f"/api/ingestion-jobs/{job_id}/start")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "job_id": job_id, "status": "queued"}
    assert store.get_job(job_id)["status"] == "queued"
    assert runner.enqueued == [job_id]
    assert runner.status_at_enqueue == ["queued"]


def test_start_storage_failure_never_enqueues(tmp_path: Path, monkeypatch) -> None:
    client, store, runner = make_client(tmp_path)
    job_id = create_draft(client)

    def fail_start(_: str) -> str:
        raise sqlite3.OperationalError("database unavailable /private/db")

    monkeypatch.setattr(store, "start_job", fail_start)

    response = client.post(f"/api/ingestion-jobs/{job_id}/start")

    assert response.status_code == 500
    assert response.json() == {"detail": "任务存储暂时不可用"}
    assert runner.enqueued == []


def test_start_enqueue_failure_keeps_durable_queued_state(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, runner = make_client(tmp_path)
    job_id = create_draft(client)

    def fail_enqueue(_: str) -> None:
        raise RuntimeError("queue unavailable /private/path token=secret")

    monkeypatch.setattr(runner, "enqueue", fail_enqueue)

    response = client.post(f"/api/ingestion-jobs/{job_id}/start")

    assert response.status_code == 500
    assert response.json() == {"detail": "任务存储暂时不可用"}
    assert store.get_job(job_id)["status"] == "queued"


def test_route_rejects_runtime_store_runner_mismatch(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    job_id = create_draft(client)
    client.app.state.ingestion_store = IngestionStore(
        tmp_path / "other.sqlite3", jobs_root=tmp_path / "other-jobs"
    )

    response = client.post(f"/api/ingestion-jobs/{job_id}/start")

    assert response.status_code == 500
    assert response.json() == {"detail": "任务存储暂时不可用"}


def test_list_detail_and_progress_use_public_shapes(tmp_path: Path) -> None:
    client, store, _ = make_client(tmp_path)
    body_sentinel = "PRIVATE-BODY-SENTINEL-7341"
    job_id = create_draft(client, content=body_sentinel.encode())
    source_sentinel = str(
        store.get_job_for_execution(job_id)["source_path"]
    )
    assert store.start_job(job_id) == "ok"
    assert store.claim_job(job_id) is True
    workspace = store.jobs_root / job_id / "attempts" / "1"
    workspace.mkdir(parents=True)
    store.begin_attempt(job_id, workspace)
    digest_sentinel = "d" * 64
    store.mark_content_identity(job_id, digest_sentinel)
    journal = workspace / "rollback" / "journal.json"
    journal.parent.mkdir()
    journal.write_text("journal", encoding="utf-8")
    store.mark_commit_state(job_id, "prepared", journal)

    listing = client.get("/api/ingestion-jobs")
    detail = client.get(f"/api/ingestion-jobs/{job_id}")
    progress = client.get(f"/api/ingestion-jobs/{job_id}/progress")

    assert listing.status_code == detail.status_code == progress.status_code == 200
    assert listing.json()["jobs"][0]["job_id"] == job_id
    assert detail.json()["items"][0]["display_name"] == "course"
    assert progress.json()["job_id"] == job_id
    serialized = repr((listing.json(), detail.json(), progress.json()))
    for forbidden in (
        "source_path",
        "workspace_path",
        "journal_path",
        "content_sha256",
        body_sentinel,
        source_sentinel,
        str(workspace),
        str(journal),
        digest_sentinel,
    ):
        assert forbidden not in serialized


def test_invalid_and_missing_job_ids_are_404_without_path_effects(tmp_path: Path) -> None:
    client, store, _ = make_client(tmp_path)

    invalid = client.get("/api/ingestion-jobs/not-a-uuid/progress")
    missing = client.get(f"/api/ingestion-jobs/{'f' * 32}")

    assert invalid.status_code == 404
    assert missing.status_code == 404
    assert store.list_jobs() == []
    assert not (tmp_path / "not-a-uuid").exists()


def test_retry_cleans_attempts_preserves_source_then_enqueues(tmp_path: Path) -> None:
    client, store, runner = make_client(tmp_path)
    job_id = create_draft(client)
    old_marker = fail_job(store, job_id)
    source_path = store.jobs_root / job_id / "source" / "course.txt"

    response = client.post(f"/api/ingestion-jobs/{job_id}/retry")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "job_id": job_id,
        "attempt_no": 2,
        "status": "queued",
    }
    assert source_path.read_bytes() == "课程内容".encode()
    assert not old_marker.exists()
    assert list((store.jobs_root / job_id / "attempts").iterdir()) == []
    assert store.get_job(job_id)["attempt_count"] == 2
    assert runner.enqueued == [job_id]


def test_retry_cleanup_failure_aborts_reservation_and_never_enqueues(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, runner = make_client(tmp_path)
    job_id = create_draft(client)
    old_marker = fail_job(store, job_id)

    def fail_cleanup(_: Path) -> None:
        raise OSError("cannot remove /private/attempt token=secret")

    monkeypatch.setattr(ingestion_routes, "clear_attempt_workspaces", fail_cleanup)

    response = client.post(f"/api/ingestion-jobs/{job_id}/retry")

    assert response.status_code == 500
    assert response.json() == {"detail": "任务存储暂时不可用"}
    job = store.get_job(job_id)
    assert job["status"] == "failed"
    assert job["attempt_count"] == 1
    assert job["error_detail"] == "清理旧任务产物失败"
    assert old_marker.exists()
    assert runner.enqueued == []


def test_delete_is_two_phase_and_file_failure_keeps_deleting(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, _ = make_client(tmp_path)
    job_id = create_draft(client)

    def fail_delete(_: Path) -> None:
        raise OSError("cannot delete /private/jobs token=secret")

    monkeypatch.setattr(ingestion_routes, "delete_job_workspace", fail_delete)

    response = client.delete(f"/api/ingestion-jobs/{job_id}")

    assert response.status_code == 500
    assert response.json() == {"detail": "任务存储暂时不可用"}
    assert store.get_job(job_id)["status"] == "deleting"
    assert (store.jobs_root / job_id).exists()


def test_delete_removes_workspace_before_finishing_record(tmp_path: Path, monkeypatch) -> None:
    client, store, _ = make_client(tmp_path)
    job_id = create_draft(client)
    original_finish = store.finish_delete

    def finish_after_asserting_workspace_is_gone(selected_job_id: str) -> None:
        assert not (store.jobs_root / selected_job_id).exists()
        original_finish(selected_job_id)

    monkeypatch.setattr(store, "finish_delete", finish_after_asserting_workspace_is_gone)

    response = client.delete(f"/api/ingestion-jobs/{job_id}")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "job_id": job_id, "status": "deleted"}
    assert store.get_job(job_id) is None


def test_concurrent_deletes_are_idempotent_without_500(tmp_path: Path) -> None:
    client, store, _ = make_client(tmp_path)
    job_id = create_draft(client)

    def delete_once(_: int) -> int:
        return TestClient(client.app).delete(
            f"/api/ingestion-jobs/{job_id}"
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(delete_once, range(8)))

    assert set(statuses) <= {200, 404}
    assert 500 not in statuses
    assert store.get_job(job_id) is None
    assert not (store.jobs_root / job_id).exists()


def test_retry_and_delete_enforce_state_conflicts(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    job_id = create_draft(client)

    assert client.post(f"/api/ingestion-jobs/{job_id}/retry").status_code == 409
    assert client.post(f"/api/ingestion-jobs/{job_id}/start").status_code == 200
    assert client.delete(f"/api/ingestion-jobs/{job_id}").status_code == 409


def test_source_file_is_regular_and_private(tmp_path: Path) -> None:
    client, store, _ = make_client(tmp_path)
    job_id = create_draft(client)

    source = store.jobs_root / job_id / "source" / "course.txt"
    metadata = source.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
