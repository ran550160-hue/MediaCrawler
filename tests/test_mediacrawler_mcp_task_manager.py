import json
import time
from pathlib import Path

import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.crawler_runner import CollectionOptions, CrawlerRunner
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.locks import acquire_xhs_profile, current_xhs_profile_owner, release_xhs_profile
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.task_manager import TaskManager


class FakeProcess:
    def __init__(self, return_code=0, on_wait=None, block_until_terminated=False):
        self.pid = 4321
        self.return_code = return_code
        self.on_wait = on_wait
        self.terminated = False
        self.block_until_terminated = block_until_terminated

    def wait(self):
        while self.block_until_terminated and not self.terminated:
            time.sleep(0.02)
        if self.on_wait:
            self.on_wait()
        return -15 if self.terminated else self.return_code

    def poll(self):
        return None if not self.terminated else -15

    def terminate(self):
        self.terminated = True


class FakeRunner(CrawlerRunner):
    def __init__(self, process):
        self.process = process
        self.started = {}

    def start(self, keywords, output_dir, log_path, options):
        self.started = {
            "keywords": keywords,
            "output_dir": output_dir,
            "log_path": log_path,
            "options": options,
        }
        return self.process


class FakeLoginManager:
    def __init__(self, status="logged_in", cookie_string=None, can_collect=True):
        self.status = status
        self.cookie_string = cookie_string
        self.can_collect = can_collect
        self.verify_remote_values = []
        self.verify_permission_values = []

    def get_login_status(self, platform, verify_remote=False, verify_permission=False):
        self.verify_remote_values.append(verify_remote)
        self.verify_permission_values.append(verify_permission)
        return {
            "status": self.status,
            "platform": platform,
            "remote_verified": verify_remote,
            "permission_verified": verify_permission,
            "can_collect": self.can_collect,
            "message": "login required" if self.status != "logged_in" else "ok",
        }

    def get_cookie_string(self, platform):
        return self.cookie_string


def _setup(tmp_path, runner, login_manager=None):
    config = McpConfig(
        home=tmp_path,
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )
    storage = Storage(config)
    dataset_service = DatasetService(config, storage)
    dataset = dataset_service.create_dataset(
        name="小红书采集任务",
        platforms=["xhs"],
        keywords=["程序员接单", "AI编程副业"],
    )
    return dataset, TaskManager(storage, runner, login_manager or FakeLoginManager()), storage


def _setup_with_config(tmp_path, runner, config, login_manager=None):
    storage = Storage(config)
    dataset_service = DatasetService(config, storage)
    dataset = dataset_service.create_dataset(
        name="cdp test",
        platforms=["xhs"],
        keywords=["AI"],
    )
    return dataset, TaskManager(storage, runner, login_manager or FakeLoginManager()), storage


def _wait_until(predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met before timeout")


def test_start_collection_runs_runner_and_archives_outputs(tmp_path):
    process_holder = {}

    def on_wait():
        output_dir = process_holder["runner"].started["output_dir"]
        jsonl_dir = output_dir / "xhs" / "jsonl"
        jsonl_dir.mkdir(parents=True)
        (jsonl_dir / "search_contents_2026-07-01.jsonl").write_text(
            json.dumps({"note_id": "n1", "source_keyword": "程序员接单"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (jsonl_dir / "search_comments_2026-07-01.jsonl").write_text(
            json.dumps({"comment_id": "c1", "note_id": "n1"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    process = FakeProcess(return_code=0, on_wait=on_wait)
    runner = FakeRunner(process)
    process_holder["runner"] = runner
    dataset, manager, _ = _setup(tmp_path, runner)

    result = manager.start_collection(
        dataset.dataset_id,
        include_comments=True,
        max_contents=12,
        max_comments_per_content=3,
        headless=False,
    )

    assert result["task_id"].startswith("task_collect_xhs_")
    assert runner.started["keywords"] == ["程序员接单", "AI编程副业"]
    assert isinstance(runner.started["options"], CollectionOptions)
    assert runner.started["options"].max_contents == 12
    assert runner.started["options"].max_comments_per_content == 3
    assert runner.started["options"].headless is False
    assert runner.started["options"].enable_cdp_mode is False
    assert runner.started["options"].cdp_connect_existing is False
    assert manager.login_manager.verify_remote_values == [True]
    assert manager.login_manager.verify_permission_values == [True]

    _wait_until(lambda: manager.get_task_status(result["task_id"])["status"] == "ready")
    status = manager.get_task_status(result["task_id"])
    assert status["progress"] == 1.0
    assert status["error_code"] is None

    raw_dir = Path(dataset.dataset_dir) / "raw"
    assert (raw_dir / "xhs_contents.jsonl").exists()
    assert (raw_dir / "xhs_comments.jsonl").exists()


def test_crawler_runner_build_command_disables_cdp_by_default(tmp_path):
    runner = CrawlerRunner(repo_root=tmp_path)
    command = runner.build_command(
        keywords=["AI编程副业"],
        output_dir=tmp_path / "out",
        options=CollectionOptions(max_contents=3, login_type="cookie", cookie_string="web_session=abc"),
    )

    assert "--enable_cdp_mode" in command
    assert command[command.index("--enable_cdp_mode") + 1] == "false"
    assert "--cdp_connect_existing" in command
    assert command[command.index("--cdp_connect_existing") + 1] == "false"
    assert command[command.index("--crawler_max_notes_count") + 1] == "3"
    assert command[command.index("--lt") + 1] == "cookie"


def test_crawler_runner_build_command_passes_cdp_debug_port(tmp_path):
    runner = CrawlerRunner(repo_root=tmp_path)
    command = runner.build_command(
        keywords=["AI缂栫▼鍓笟"],
        output_dir=tmp_path / "out",
        options=CollectionOptions(
            max_contents=3,
            login_type="cookie",
            cookie_string="web_session=abc",
            enable_cdp_mode=True,
            cdp_connect_existing=True,
            cdp_debug_port=9333,
        ),
    )

    assert "--cdp_debug_port" in command
    assert command[command.index("--cdp_debug_port") + 1] == "9333"


def test_archive_outputs_trims_contents_and_related_comments(tmp_path):
    output_dir = tmp_path / "output"
    jsonl_dir = output_dir / "xhs" / "jsonl"
    jsonl_dir.mkdir(parents=True)
    contents = [
        {"note_id": "n1", "title": "one"},
        {"note_id": "n2", "title": "two"},
        {"note_id": "n3", "title": "three"},
    ]
    comments = [
        {"comment_id": "c1", "note_id": "n1"},
        {"comment_id": "c2", "note_id": "n2"},
        {"comment_id": "c3", "note_id": "n3"},
    ]
    (jsonl_dir / "search_contents_2026-07-01.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in contents),
        encoding="utf-8",
    )
    (jsonl_dir / "search_comments_2026-07-01.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in comments),
        encoding="utf-8",
    )

    raw_dir = tmp_path / "raw"
    archived = CrawlerRunner(repo_root=tmp_path).archive_outputs(output_dir, raw_dir, max_contents=2)

    assert set(archived) >= {"contents", "comments"}
    assert archived["output_layout"] == "legacy_shared"
    assert archived["run_id"] == ""
    content_rows = [
        json.loads(line)
        for line in (raw_dir / "xhs_contents.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    comment_rows = [
        json.loads(line)
        for line in (raw_dir / "xhs_comments.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["note_id"] for row in content_rows] == ["n1", "n2"]
    assert [row["note_id"] for row in comment_rows] == ["n1", "n2"]


def _write_run_directory(run_dir: Path, run_id: str, contents: list[dict], comments: list[dict]) -> None:
    jsonl_dir = run_dir / "jsonl"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_metadata.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    (jsonl_dir / "search_contents.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in contents), encoding="utf-8"
    )
    (jsonl_dir / "search_comments.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in comments), encoding="utf-8"
    )


def test_archive_outputs_discovers_run_isolated_layout(tmp_path):
    output_dir = tmp_path / "output"
    run_dir = output_dir / "xhs" / "run_20260711_180626_016ab9"
    _write_run_directory(
        run_dir,
        "run_20260711_180626_016ab9",
        [{"note_id": "n1", "title": "one"}, {"note_id": "n2", "title": "two"}],
        [{"comment_id": "c1", "note_id": "n1"}, {"comment_id": "c2", "note_id": "n2"}],
    )

    raw_dir = tmp_path / "raw"
    archived = CrawlerRunner(repo_root=tmp_path).archive_outputs(output_dir, raw_dir)

    assert archived["output_layout"] == "run_isolated"
    assert archived["run_id"] == "run_20260711_180626_016ab9"
    content_rows = [
        json.loads(line)
        for line in (raw_dir / "xhs_contents.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    comment_rows = [
        json.loads(line)
        for line in (raw_dir / "xhs_comments.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["note_id"] for row in content_rows] == ["n1", "n2"]
    assert [row["note_id"] for row in comment_rows] == ["n1", "n2"]


def test_archive_outputs_refuses_to_silently_merge_multiple_runs(tmp_path):
    output_dir = tmp_path / "output"
    _write_run_directory(
        output_dir / "xhs" / "run_aaa", "run_aaa", [{"note_id": "a1"}], [{"comment_id": "a1c", "note_id": "a1"}]
    )
    _write_run_directory(
        output_dir / "xhs" / "run_bbb", "run_bbb", [{"note_id": "b1"}], [{"comment_id": "b1c", "note_id": "b1"}]
    )

    raw_dir = tmp_path / "raw"
    with pytest.raises(McpAppError) as exc_info:
        CrawlerRunner(repo_root=tmp_path).archive_outputs(output_dir, raw_dir)

    assert exc_info.value.code == ErrorCode.CRAWLER_FAILED
    assert "refusing to silently merge" in exc_info.value.message
    assert "run_aaa" in exc_info.value.detail
    assert "run_bbb" in exc_info.value.detail


def test_archive_outputs_skips_empty_runs_when_selecting(tmp_path):
    output_dir = tmp_path / "output"
    (output_dir / "xhs" / "run_empty" / "jsonl").mkdir(parents=True)
    (output_dir / "xhs" / "run_empty" / "run_metadata.json").write_text("{}", encoding="utf-8")
    _write_run_directory(
        output_dir / "xhs" / "run_good", "run_good", [{"note_id": "n1"}], [{"comment_id": "c1", "note_id": "n1"}]
    )

    raw_dir = tmp_path / "raw"
    archived = CrawlerRunner(repo_root=tmp_path).archive_outputs(output_dir, raw_dir)

    assert archived["run_id"] == "run_good"


def test_archive_outputs_picks_most_recent_run_when_unambiguous(tmp_path):
    import os
    import time as _time

    output_dir = tmp_path / "output"
    _write_run_directory(
        output_dir / "xhs" / "run_older", "run_older", [{"note_id": "o1"}], [{"comment_id": "oc", "note_id": "o1"}]
    )
    _time.sleep(1.1)
    _write_run_directory(
        output_dir / "xhs" / "run_newer", "run_newer", [{"note_id": "n1"}], [{"comment_id": "nc", "note_id": "n1"}]
    )

    raw_dir = tmp_path / "raw"
    archived = CrawlerRunner(repo_root=tmp_path).archive_outputs(output_dir, raw_dir)

    assert archived["run_id"] == "run_newer"
    content_rows = [
        json.loads(line)
        for line in (raw_dir / "xhs_contents.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["note_id"] for row in content_rows] == ["n1"]


def test_start_collection_marks_failed_when_process_fails(tmp_path):
    runner = FakeRunner(FakeProcess(return_code=2))
    dataset, manager, _ = _setup(tmp_path, runner)

    result = manager.start_collection(dataset.dataset_id)

    _wait_until(lambda: manager.get_task_status(result["task_id"])["status"] == "failed")
    status = manager.get_task_status(result["task_id"])
    assert status["error_code"] == ErrorCode.CRAWLER_FAILED
    assert "exited with code 2" in status["error_message"]


def test_cancel_task_terminates_running_process(tmp_path):
    process = FakeProcess(return_code=0, block_until_terminated=True)
    runner = FakeRunner(process)
    dataset, manager, _ = _setup(tmp_path, runner)

    result = manager.start_collection(dataset.dataset_id)
    cancel_result = manager.cancel_task(result["task_id"])

    assert cancel_result["status"] == "cancelled"
    assert process.terminated is True
    assert manager.get_task_status(result["task_id"])["status"] == "cancelled"


def test_get_task_status_returns_structured_error_for_missing_task(tmp_path):
    runner = FakeRunner(FakeProcess())
    _, manager, _ = _setup(tmp_path, runner)

    with pytest.raises(McpAppError) as exc_info:
        manager.get_task_status("missing")

    assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND


def test_start_collection_validates_dataset_id(tmp_path):
    runner = FakeRunner(FakeProcess())
    _, manager, _ = _setup(tmp_path, runner)

    with pytest.raises(McpAppError) as exc_info:
        manager.start_collection("")

    assert exc_info.value.code == ErrorCode.INVALID_ARGUMENT


def test_start_collection_requires_login_before_starting_runner(tmp_path):
    runner = FakeRunner(FakeProcess())
    dataset, manager, _ = _setup(tmp_path, runner, FakeLoginManager(status="logged_out"))

    with pytest.raises(McpAppError) as exc_info:
        manager.start_collection(dataset.dataset_id)

    assert exc_info.value.code == ErrorCode.PREFLIGHT_FAILED
    assert exc_info.value.payload["checks"][1]["name"] == "xhs_login"
    assert runner.started == {}


def test_start_collection_rejects_when_xhs_profile_is_busy(tmp_path):
    runner = FakeRunner(FakeProcess())
    dataset, manager, _ = _setup(tmp_path, runner)
    owner = "test:busy"
    assert acquire_xhs_profile(manager.storage.config, owner) is True
    try:
        with pytest.raises(McpAppError) as exc_info:
            manager.start_collection(dataset.dataset_id)

        assert exc_info.value.code == ErrorCode.RESOURCE_BUSY
        assert runner.started == {}
    finally:
        release_xhs_profile(owner)


def test_start_collection_holds_profile_lock_until_process_finishes(tmp_path):
    process = FakeProcess(return_code=0, block_until_terminated=True)
    runner = FakeRunner(process)
    dataset, manager, _ = _setup(tmp_path, runner)

    result = manager.start_collection(dataset.dataset_id)

    assert current_xhs_profile_owner(manager.storage.config) == f"collection:{result['task_id']}"
    manager.cancel_task(result["task_id"])
    _wait_until(lambda: current_xhs_profile_owner(manager.storage.config) is None)


def test_start_collection_does_not_continue_with_expired_cookie_status(tmp_path):
    runner = FakeRunner(FakeProcess())
    dataset, manager, _ = _setup(
        tmp_path,
        runner,
        FakeLoginManager(status="expired", cookie_string=None),
    )

    with pytest.raises(McpAppError) as exc_info:
        manager.start_collection(dataset.dataset_id)

    assert exc_info.value.code == ErrorCode.PREFLIGHT_FAILED
    assert runner.started == {}


def test_start_collection_uses_imported_cookie_login(tmp_path):
    runner = FakeRunner(FakeProcess(return_code=2))
    dataset, manager, _ = _setup(
        tmp_path,
        runner,
        FakeLoginManager(status="logged_in", cookie_string="web_session=abc123; a=b"),
    )

    result = manager.start_collection(dataset.dataset_id)

    assert result["task_id"].startswith("task_collect_xhs_")
    assert runner.started["options"].login_type == "cookie"
    assert runner.started["options"].cookie_string == "web_session=abc123; a=b"


def test_start_collection_verifies_login_remotely_by_default(tmp_path):
    runner = FakeRunner(FakeProcess(return_code=2))
    login_manager = FakeLoginManager(status="logged_in", cookie_string="web_session=abc123")
    dataset, manager, _ = _setup(tmp_path, runner, login_manager)

    manager.start_collection(dataset.dataset_id)

    assert login_manager.verify_remote_values == [True]
    assert login_manager.verify_permission_values == [True]


def test_start_collection_can_disable_remote_verify(tmp_path):
    runner = FakeRunner(FakeProcess(return_code=2))
    login_manager = FakeLoginManager(status="logged_in", cookie_string="web_session=abc123")
    dataset, manager, _ = _setup(tmp_path, runner, login_manager)

    manager.start_collection(dataset.dataset_id, verify_login_remote=False)

    assert login_manager.verify_remote_values == [False]
    assert login_manager.verify_permission_values == [True]


def test_start_collection_blocks_when_remote_verify_marks_expired(tmp_path):
    runner = FakeRunner(FakeProcess())
    login_manager = FakeLoginManager(status="expired", cookie_string="web_session=abc123")
    dataset, manager, _ = _setup(tmp_path, runner, login_manager)

    with pytest.raises(McpAppError) as exc_info:
        manager.start_collection(dataset.dataset_id, verify_login_remote=True)

    assert exc_info.value.code == ErrorCode.PREFLIGHT_FAILED
    assert login_manager.verify_remote_values == [True]
    assert runner.started == {}


def test_start_collection_skip_preflight_runs_native_crawler_for_diagnostics(tmp_path):
    runner = FakeRunner(FakeProcess(return_code=2))
    login_manager = FakeLoginManager(status="permission_denied", cookie_string=None, can_collect=False)
    dataset, manager, _ = _setup(tmp_path, runner, login_manager)

    result = manager.start_collection(dataset.dataset_id, skip_preflight=True, max_contents=1, include_comments=False)

    assert result["task_id"].startswith("task_collect_xhs_")
    assert login_manager.verify_remote_values == []
    assert login_manager.verify_permission_values == []
    assert runner.started["options"].login_type == "qrcode"
    assert runner.started["options"].max_contents == 1
    assert runner.started["options"].include_comments is False


def test_start_collection_preflight_fails_when_cdp_endpoint_unreachable(tmp_path):
    config = McpConfig(
        home=tmp_path,
        browser_mode="cdp_existing",
        cdp_endpoint="http://127.0.0.1:9",
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )
    runner = FakeRunner(FakeProcess())
    dataset, manager, _ = _setup_with_config(tmp_path, runner, config)

    with pytest.raises(McpAppError) as exc_info:
        manager.start_collection(dataset.dataset_id)

    assert exc_info.value.code == ErrorCode.PREFLIGHT_FAILED
    assert any(check["name"] == "cdp_endpoint" and check["status"] == "failed" for check in exc_info.value.payload["checks"])
    assert runner.started == {}


def test_get_task_status_guides_qr_login_task_ids(tmp_path):
    runner = FakeRunner(FakeProcess())
    _, manager, _ = _setup(tmp_path, runner)

    with pytest.raises(McpAppError) as exc_info:
        manager.get_task_status("task_qrcode_login_xhs_20260704_abc")

    assert exc_info.value.code == ErrorCode.TASK_TYPE_MISMATCH
    assert "get_qrcode_login_status" in exc_info.value.message


def test_start_collection_preflight_blocks_permission_denied(tmp_path):
    runner = FakeRunner(FakeProcess())
    dataset, manager, _ = _setup(
        tmp_path,
        runner,
        FakeLoginManager(status="permission_denied", cookie_string="web_session=abc123", can_collect=False),
    )

    with pytest.raises(McpAppError) as exc_info:
        manager.start_collection(dataset.dataset_id)

    assert exc_info.value.code == ErrorCode.PREFLIGHT_FAILED
    assert any(check["name"] == "xhs_login" and check["status"] == "failed" for check in exc_info.value.payload["checks"])
    assert runner.started == {}


def test_failed_crawler_log_parser_extracts_permission_denied(tmp_path):
    log_path = tmp_path / "crawler.log"
    log_path.write_text("DataFetchError: 您当前登录的账号没有权限访问\n", encoding="utf-8")

    code, message = TaskManager._extract_crawler_error(log_path)

    assert code == ErrorCode.XHS_PERMISSION_DENIED
    assert message == "您当前登录的账号没有权限访问"
