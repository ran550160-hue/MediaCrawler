import json

import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.storage import Storage


def _service(tmp_path):
    config = McpConfig(
        home=tmp_path,
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )
    storage = Storage(config)
    return DatasetService(config, storage), storage


def test_create_dataset_creates_directory_json_and_sqlite_row(tmp_path):
    service, storage = _service(tmp_path)

    dataset = service.create_dataset(
        name="AI 编程副业真实反馈调研",
        platforms=["xhs"],
        keywords=["AI编程副业", "程序员接单"],
        description="小红书测试数据集",
    )

    dataset_dir = tmp_path / "datasets" / dataset.dataset_id
    dataset_json = dataset_dir / "dataset.json"

    assert dataset.dataset_id.startswith("ds_")
    assert dataset.status == "created"
    assert dataset.platforms == ["xhs"]
    assert dataset.keywords == ["AI编程副业", "程序员接单"]
    assert dataset_dir.is_dir()
    assert (dataset_dir / "raw").is_dir()
    assert (dataset_dir / "logs").is_dir()
    assert (dataset_dir / "reports").is_dir()
    assert dataset_json.exists()

    payload = json.loads(dataset_json.read_text(encoding="utf-8"))
    assert payload["dataset_id"] == dataset.dataset_id
    assert payload["name"] == "AI 编程副业真实反馈调研"
    assert payload["description"] == "小红书测试数据集"
    assert payload["status"] == "created"
    assert payload["platforms"] == ["xhs"]
    assert payload["files"] == {"raw": {}, "reports": {}}
    assert payload["metrics"] == {"content_count": 0, "comment_count": 0}
    assert payload["warnings"] == []
    assert payload["errors"] == []

    row = storage.get_dataset_row(dataset.dataset_id)
    assert row is not None
    assert row["dataset_id"] == dataset.dataset_id
    assert row["name"] == "AI 编程副业真实反馈调研"
    assert json.loads(row["platforms_json"]) == ["xhs"]
    assert json.loads(row["keywords_json"]) == ["AI编程副业", "程序员接单"]
    assert row["content_count"] == 0
    assert row["comment_count"] == 0


@pytest.mark.parametrize(
    ("name", "platforms", "keywords", "code"),
    [
        ("", ["xhs"], ["编程副业"], ErrorCode.INVALID_ARGUMENT),
        ("测试", [], ["编程副业"], ErrorCode.INVALID_ARGUMENT),
        ("测试", ["xhs"], [], ErrorCode.INVALID_ARGUMENT),
        ("测试", ["dy"], ["编程副业"], ErrorCode.UNSUPPORTED_PLATFORM),
        ("测试", ["xhs", "dy"], ["编程副业"], ErrorCode.UNSUPPORTED_PLATFORM),
    ],
)
def test_create_dataset_returns_structured_errors_for_invalid_inputs(
    tmp_path, name, platforms, keywords, code
):
    service, _ = _service(tmp_path)

    with pytest.raises(McpAppError) as exc_info:
        service.create_dataset(name=name, platforms=platforms, keywords=keywords)

    assert exc_info.value.code == code
    result = exc_info.value.to_result()
    assert result["status"] == "failed"
    assert result["error"]["code"] == code


def test_list_datasets_supports_filters_and_limit(tmp_path):
    service, _ = _service(tmp_path)
    first = service.create_dataset(
        name="AI 编程副业",
        platforms=["xhs"],
        keywords=["AI编程副业"],
    )
    second = service.create_dataset(
        name="Python 接单调研",
        platforms=["xhs"],
        keywords=["Python接单"],
    )

    all_datasets = service.list_datasets()
    assert [item["dataset_id"] for item in all_datasets] == [second.dataset_id, first.dataset_id]

    keyword_results = service.list_datasets(keyword="Python")
    assert [item["dataset_id"] for item in keyword_results] == [second.dataset_id]

    platform_results = service.list_datasets(platform="xhs")
    assert len(platform_results) == 2

    status_results = service.list_datasets(status="created", limit=1)
    assert len(status_results) == 1
    assert status_results[0]["dataset_id"] == second.dataset_id


def test_get_dataset_returns_json_payload_and_sqlite_summary(tmp_path):
    service, _ = _service(tmp_path)
    dataset = service.create_dataset(
        name="AI 编程副业",
        platforms=["xhs"],
        keywords=["AI编程副业"],
        description="测试详情",
    )

    result = service.get_dataset(dataset.dataset_id)

    assert result["dataset_id"] == dataset.dataset_id
    assert result["name"] == "AI 编程副业"
    assert result["description"] == "测试详情"
    assert result["platforms"] == ["xhs"]
    assert result["keywords"] == ["AI编程副业"]
    assert result["dataset_json_path"].endswith("dataset.json")
    assert result["sqlite"]["dataset_id"] == dataset.dataset_id
    assert result["sqlite"]["content_count"] == 0


def test_get_dataset_returns_structured_error_for_missing_dataset(tmp_path):
    service, _ = _service(tmp_path)

    with pytest.raises(McpAppError) as exc_info:
        service.get_dataset("missing")

    assert exc_info.value.code == ErrorCode.DATASET_NOT_FOUND
    result = exc_info.value.to_result()
    assert result["status"] == "failed"
    assert result["error"]["code"] == ErrorCode.DATASET_NOT_FOUND


def test_get_dataset_requires_dataset_id(tmp_path):
    service, _ = _service(tmp_path)

    with pytest.raises(McpAppError) as exc_info:
        service.get_dataset("")

    assert exc_info.value.code == ErrorCode.INVALID_ARGUMENT
