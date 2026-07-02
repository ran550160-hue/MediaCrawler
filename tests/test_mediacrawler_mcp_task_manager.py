import json
import time
from pathlib import Path

import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.crawler_runner import CollectionOptions, CrawlerRunner
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.errors import ErrorCode, McpAppError
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
    def __init__(self, status="logged_in", cookie_string=None):
        self.status = status
        self.cookie_string = cookie_string

    def get_login_status(self, platform):
        return {
            "status": self.status,
            "platform": platform,
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

    assert set(archived) == {"contents", "comments"}
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

    assert exc_info.value.code == ErrorCode.LOGIN_REQUIRED
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
