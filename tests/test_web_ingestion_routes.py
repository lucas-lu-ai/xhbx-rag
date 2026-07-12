from __future__ import annotations

import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi.testclient import TestClient

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


def test_list_detail_and_progress_use_public_shapes(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    job_id = create_draft(client)

    listing = client.get("/api/ingestion-jobs")
    detail = client.get(f"/api/ingestion-jobs/{job_id}")
    progress = client.get(f"/api/ingestion-jobs/{job_id}/progress")

    assert listing.status_code == detail.status_code == progress.status_code == 200
    assert listing.json()["jobs"][0]["job_id"] == job_id
    assert detail.json()["items"][0]["display_name"] == "course"
    assert progress.json()["job_id"] == job_id
    serialized = repr((listing.json(), detail.json(), progress.json()))
    for forbidden in ("source_path", "workspace_path", "journal_path", "content_sha256"):
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
