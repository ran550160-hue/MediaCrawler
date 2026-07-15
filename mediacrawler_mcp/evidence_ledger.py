from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

import duckdb

from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.models import DiscoveryContext, EvidenceItem, EvidenceObservation, ResearchRun
from mediacrawler_mcp.normalizer import RAW_FILE_NAMES
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import utc_now_iso


LEDGER_SCHEMA_VERSION = "evidence-ledger/v2"
NORMALIZATION_VERSION = "mediacrawler_mcp.normalizer/contents-v1"
HASH_ALGORITHM_VERSION = "sha256-canonical-json-nfc-v1"
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
DEFAULT_FULL_TEXT_LIMIT = 1_000


class ClaimGenerator(Protocol):
    """Reserved seam for a later PR; implementations must cite evidence IDs."""

    def generate_claims(self, research_run: ResearchRun, evidence_items: list[EvidenceItem]) -> list[dict[str, Any]]:
        """Return claims whose citations refer to immutable ``evidence_id`` values."""


class EvidenceLedger:
    """The seam that converts normalized content into immutable evidence versions."""

    def __init__(self, storage: Storage):
        self.storage = storage

    def create_research_run(
        self,
        question: str,
        platforms: list[str],
        keywords: list[str],
        time_range: dict[str, Any] | None = None,
        sample_limits: dict[str, Any] | None = None,
        collection_task_ids: list[str] | None = None,
        dataset_version: str | None = None,
    ) -> ResearchRun:
        question = (question or "").strip()
        clean_platforms = self._strings(platforms)
        if not question:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Research question is required")
        if not clean_platforms:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "At least one research platform is required")
        self.storage.initialize()
        run = ResearchRun(
            research_run_id=f"research_{uuid.uuid4().hex[:16]}",
            question=question,
            platforms=clean_platforms,
            keywords=self._strings(keywords),
            time_range=time_range if isinstance(time_range, dict) else None,
            sample_limits=sample_limits if isinstance(sample_limits, dict) else {},
            created_at=utc_now_iso(),
            status="created",
            collection_task_ids=self._strings(collection_task_ids or []),
            dataset_version=(dataset_version or "unbound").strip() or "unbound",
            ledger_schema_version=LEDGER_SCHEMA_VERSION,
            hash_algorithm_version=HASH_ALGORITHM_VERSION,
        )
        self.storage.create_research_run(run)
        return run

    def get_research_run(self, research_run_id: str) -> ResearchRun:
        self.storage.initialize()
        research_run_id = self._required_id(research_run_id, "Research run ID")
        row = self.storage.get_research_run_row(research_run_id)
        if row is None:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Research run not found", research_run_id)
        return self._research_run_from_row(row)

    def build_evidence_items(self, research_run_id: str, dataset_id: str) -> dict[str, Any]:
        """Build once, freeze on completion, and atomically persist all evidence."""
        run = self.get_research_run(research_run_id)
        dataset_id = self._required_id(dataset_id, "Dataset ID")
        dataset = self.storage.get_dataset_row(dataset_id)
        if dataset is None:
            raise McpAppError(ErrorCode.DATASET_NOT_FOUND, "Dataset not found", f"dataset_id={dataset_id}")
        dataset_dir = Path(dataset["dataset_dir"])
        duckdb_path = dataset_dir / "analysis.duckdb"
        if not duckdb_path.exists():
            raise McpAppError(ErrorCode.QUERY_FAILED, "Dataset is not normalized", "Call normalize_dataset before building an evidence ledger")

        dataset_version = self._dataset_version(dataset_id, dataset_dir, duckdb_path)
        if run.status == "completed":
            if run.source_dataset_id == dataset_id and run.dataset_version == dataset_version:
                _, total = self.storage.list_evidence_version_rows(
                    research_run_id, platform=None, content_type=None, limit=1, offset=0
                )
                return {
                    "research_run_id": research_run_id,
                    "dataset_id": dataset_id,
                    "dataset_version": dataset_version,
                    "evidence_count": total,
                    "already_completed": True,
                    "status": "completed",
                }
            raise McpAppError(
                ErrorCode.INVALID_ARGUMENT,
                "Research run evidence ledger is frozen",
                "Create a new ResearchRun before building from a changed dataset snapshot.",
            )
        if run.status == "building":
            raise McpAppError(
                ErrorCode.INVALID_ARGUMENT,
                "Research run build is already in progress",
                "Use recover_stale_build only after confirming the previous process exited.",
            )
        if run.status not in {"created", "build_failed"}:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Research run cannot be built", f"status={run.status}")

        claimed = self.storage.mark_evidence_build_started(
            research_run_id,
            source_dataset_id=dataset_id,
            dataset_version=dataset_version,
            normalization_version=NORMALIZATION_VERSION,
            ledger_schema_version=LEDGER_SCHEMA_VERSION,
            hash_algorithm_version=HASH_ALGORITHM_VERSION,
            started_at=utc_now_iso(),
        )
        if not claimed:
            current = self.get_research_run(research_run_id)
            if current.status == "building":
                raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Research run build is already in progress")
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Research run cannot acquire build lease", f"status={current.status}")
        try:
            normalized_rows = self._normalized_contents(duckdb_path, dataset_id)
            manifest = self._read_json_object(dataset_dir / "dataset.json")
            raw_records = self._raw_records_by_content_id(dataset_dir, normalized_rows)
            evidence_items: list[EvidenceItem] = []
            discovery_contexts: list[DiscoveryContext] = []
            observations: list[EvidenceObservation] = []
            seen_evidence: set[str] = set()
            for normalized in normalized_rows:
                platform = self._text(normalized.get("platform")).lower()
                if platform not in run.platforms:
                    continue
                evidence = self._from_normalized_content(run, dataset_id, normalized)
                if evidence.evidence_id not in seen_evidence:
                    evidence_items.append(evidence)
                    seen_evidence.add(evidence.evidence_id)
                source_records = raw_records.get((platform, evidence.content_id), []) or [self._json_object(normalized.get("raw_json"))]
                for source_record in source_records:
                    reference = self._raw_record_reference(dataset_id, normalized, source_record)
                    discovery_contexts.append(self._discovery_context(evidence.evidence_id, normalized, source_record, manifest))
                    observations.append(self._observation(evidence.evidence_id, normalized, source_record, reference))
            if self._dataset_version(dataset_id, dataset_dir, duckdb_path) != dataset_version:
                raise McpAppError(
                    ErrorCode.INVALID_ARGUMENT,
                    "Source snapshot changed during evidence build",
                    "Retry after normalization and raw files are stable.",
                )
            self.storage.complete_evidence_build(
                research_run_id,
                evidence_items,
                self._dedupe_by_id(discovery_contexts, "discovery_id"),
                self._dedupe_by_id(observations, "observation_id"),
                utc_now_iso(),
            )
        except Exception as exc:
            self.storage.mark_evidence_build_failed(research_run_id, str(exc))
            raise
        return {
            "research_run_id": research_run_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "evidence_count": len(evidence_items),
            "evidence_ids": [item.evidence_id for item in evidence_items],
            "already_completed": False,
            "status": "completed",
        }

    def recover_stale_build(self, research_run_id: str, expected_build_started_at: str, min_age_seconds: int = 300) -> dict[str, Any]:
        """Release only a named, expired build lease after its process interruption."""
        run = self.get_research_run(research_run_id)
        if run.status != "building":
            return {"research_run_id": research_run_id, "recovered": False, "status": run.status}
        if not expected_build_started_at or run.build_started_at != expected_build_started_at:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Build attempt does not match the active lease")
        started = datetime.fromisoformat(expected_build_started_at.replace("Z", "+00:00"))
        now = datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
        if (now - started).total_seconds() < max(0, int(min_age_seconds)):
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Build lease is not old enough to recover")
        recovered = self.storage.recover_stale_evidence_build(
            research_run_id, "Build lease manually recovered after process interruption"
        )
        return {"research_run_id": research_run_id, "recovered": recovered, "status": "build_failed" if recovered else "building"}

    def list_evidence_items(
        self,
        research_run_id: str,
        *,
        platform: str | None = None,
        content_type: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        full_text_limit: int = DEFAULT_FULL_TEXT_LIMIT,
    ) -> dict[str, Any]:
        self.get_research_run(research_run_id)
        limit = self._page_limit(limit)
        offset = max(0, int(offset or 0))
        full_text_limit = max(0, min(int(full_text_limit or DEFAULT_FULL_TEXT_LIMIT), DEFAULT_FULL_TEXT_LIMIT))
        rows, total = self.storage.list_evidence_version_rows(
            research_run_id,
            platform=self._text_or_none(platform),
            content_type=self._text_or_none(content_type),
            limit=limit,
            offset=offset,
        )
        items = [self._summary_from_evidence(self._evidence_from_row(row), full_text_limit) for row in rows]
        return {
            "evidence_items": items,
            "limit": limit,
            "offset": offset,
            "total": total,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
        }

    def get_evidence_item(self, research_run_id: str, evidence_id: str) -> EvidenceItem:
        self.get_research_run(research_run_id)
        row = self.storage.get_evidence_version_row(research_run_id, self._required_id(evidence_id, "Evidence ID"))
        if row is None:
            raise McpAppError(ErrorCode.NOT_FOUND, "Evidence item not found", evidence_id)
        return self._evidence_from_row(row, include_details=True)

    def list_evidence_versions(
        self, platform: str, content_type: str, content_id: str, limit: int = DEFAULT_PAGE_LIMIT
    ) -> dict[str, Any]:
        """Find immutable versions of one platform content entity across research runs."""
        platform = self._required_id(platform, "Platform").lower()
        content_type = self._required_id(content_type, "Content type")
        content_id = self._required_id(content_id, "Content ID")
        source_entity_id = self._source_entity_id(platform, content_type, content_id, {})
        rows = self.storage.list_evidence_version_rows_by_source_entity(source_entity_id, self._page_limit(limit))
        return {
            "source_entity_id": source_entity_id,
            "evidence_items": [self._summary_from_evidence(self._evidence_from_row(row), DEFAULT_FULL_TEXT_LIMIT) for row in rows],
        }

    def resolve_raw_record_path(self, raw_record_reference: dict[str, Any], datasets_root: str | Path | None = None) -> Path:
        """Resolve portable provenance with the current datasets root, never a stored absolute path."""
        dataset_id = self._required_id(str(raw_record_reference.get("dataset_id") or ""), "Reference dataset ID")
        relative_path = self._required_id(str(raw_record_reference.get("dataset_relative_raw_path") or ""), "Reference raw path")
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Reference raw path must stay inside its dataset")
        root = Path(datasets_root) if datasets_root is not None else self.storage.config.datasets_dir
        dataset_root = (root / dataset_id).resolve()
        candidate = (dataset_root / relative).resolve()
        try:
            candidate.relative_to(dataset_root)
        except ValueError as exc:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Reference raw path escapes its dataset") from exc
        return candidate

    def _normalized_contents(self, duckdb_path: Path, dataset_id: str) -> list[dict[str, Any]]:
        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                columns_present = {item[0] for item in conn.execute("DESCRIBE contents").fetchall()}
                if "dataset_id" not in columns_present:
                    raise McpAppError(ErrorCode.QUERY_FAILED, "Normalized contents schema is missing dataset_id")
                cursor = conn.execute("SELECT * FROM contents WHERE dataset_id = ?", [dataset_id])
                columns = [item[0] for item in cursor.description]
                rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
                conn.execute("COMMIT")
                return rows
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _raw_records_by_content_id(self, dataset_dir: Path, normalized_rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        platforms = {self._text(row.get("platform")).lower() for row in normalized_rows}
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for platform in platforms:
            raw_name = (RAW_FILE_NAMES.get(platform) or {}).get("contents")
            if not raw_name:
                continue
            id_field = self._platform_id_field(platform)
            for record in self._read_jsonl(dataset_dir / "raw" / raw_name):
                content_id = self._text(record.get(id_field) or record.get("content_id") or record.get("id"))
                if content_id:
                    grouped.setdefault((platform, content_id), []).append(record)
        return grouped

    def _from_normalized_content(self, run: ResearchRun, dataset_id: str, normalized: dict[str, Any]) -> EvidenceItem:
        platform = self._text(normalized.get("platform")).lower()
        content_type = self._text(normalized.get("content_type")) or "content"
        content_id = self._text(normalized.get("content_id"))
        raw_record = self._json_object(normalized.get("raw_json"))
        source_entity_id = self._source_entity_id(platform, content_type, content_id, raw_record)
        title = self._text(normalized.get("title"))
        full_text = self._text(normalized.get("content_text")) or self._text(normalized.get("desc")) or title
        content_hash = self._hash({"title": self._canonical_text(title), "full_text": self._canonical_text(full_text)})
        evidence_id = f"evidence_{self._hash({'research_run_id': run.research_run_id, 'source_entity_id': source_entity_id, 'content_hash': content_hash})[:24]}"
        content_revision_id = f"revision_{self._hash({'source_entity_id': source_entity_id, 'content_hash': content_hash, 'hash_algorithm_version': HASH_ALGORITHM_VERSION})[:24]}"
        published_at = self._timestamp(normalized.get("publish_datetime")) or self._text_or_none(normalized.get("publish_time"))
        collected_at = self._text_or_none(normalized.get("crawl_time"))
        canonical_content_hash = self._hash(
            {
                "source_entity_id": source_entity_id,
                "platform": platform,
                "content_type": content_type,
                "author_id": self._text(normalized.get("author_id")),
                "source_url": self._text(normalized.get("url")),
                "published_at": published_at,
                "title": self._canonical_text(title),
                "full_text": self._canonical_text(full_text),
            }
        )
        reference = self._raw_record_reference(dataset_id, normalized, raw_record)
        return EvidenceItem(
            evidence_id=evidence_id,
            research_run_id=run.research_run_id,
            source_entity_id=source_entity_id,
            platform=platform,
            content_type=content_type,
            content_id=content_id,
            author_id=self._text(normalized.get("author_id")),
            source_url=self._text(normalized.get("url")),
            published_at=published_at,
            collected_at=collected_at,
            query_keyword=self._text(normalized.get("source_keyword")),
            title=title,
            full_text=full_text,
            content_hash=content_hash,
            raw_record_hash=reference["raw_record_hash"],
            canonical_content_hash=canonical_content_hash,
            content_revision_id=content_revision_id,
            hash_algorithm_version=HASH_ALGORITHM_VERSION,
            engagement=self._engagement(normalized),
            raw_record_reference=reference,
            metadata={"raw": raw_record, "normalization": {"dataset_id": dataset_id, "author_name": self._text(normalized.get("author_name")), "tags": self._json_value(normalized.get("tags"))}},
        )

    def _raw_record_reference(self, dataset_id: str, normalized: dict[str, Any], raw_record: dict[str, Any]) -> dict[str, Any]:
        platform = self._text(normalized.get("platform")).lower()
        id_field = self._platform_id_field(platform)
        raw_name = (RAW_FILE_NAMES.get(platform) or {}).get("contents", f"{platform}_contents.jsonl")
        return {
            "dataset_id": dataset_id,
            "collection_task_id": self._text(raw_record.get("collection_task_id") or raw_record.get("task_id") or normalized.get("collection_task_id")),
            "dataset_relative_raw_path": (Path("raw") / raw_name).as_posix(),
            "normalized_table": "contents",
            "platform_record_id_field": id_field,
            "platform_record_id": self._text(raw_record.get(id_field) or raw_record.get("content_id") or normalized.get("content_id")),
            "raw_record_hash": self._hash(raw_record),
        }

    def _discovery_context(self, evidence_id: str, normalized: dict[str, Any], raw_record: dict[str, Any], manifest: dict[str, Any]) -> DiscoveryContext:
        options = manifest.get("options") if isinstance(manifest.get("options"), dict) else {}
        keyword = self._text(raw_record.get("source_keyword") or normalized.get("source_keyword"))
        collection_task_id = self._text(raw_record.get("collection_task_id") or raw_record.get("task_id") or normalized.get("collection_task_id") or options.get("collection_task_id"))
        discovered_at = self._text_or_none(raw_record.get("discovered_at") or raw_record.get("crawl_time") or normalized.get("crawl_time") or options.get("collection_completed_at") or options.get("collection_started_at"))
        position = self._optional_int(raw_record.get("result_position") or raw_record.get("position"))
        identity = {"evidence_id": evidence_id, "keyword": keyword, "collection_task_id": collection_task_id, "discovered_at": discovered_at, "result_position": position}
        return DiscoveryContext(f"discovery_{self._hash(identity)[:24]}", evidence_id, keyword, collection_task_id, discovered_at, position)

    def _observation(self, evidence_id: str, normalized: dict[str, Any], raw_record: dict[str, Any], reference: dict[str, Any]) -> EvidenceObservation:
        observed_at = self._text_or_none(raw_record.get("observed_at") or raw_record.get("crawl_time") or normalized.get("crawl_time"))
        raw_hash = reference["raw_record_hash"]
        observation_id = f"observation_{self._hash({'evidence_id': evidence_id, 'raw_record_hash': raw_hash})[:24]}"
        return EvidenceObservation(observation_id, evidence_id, raw_hash, observed_at, self._engagement_from_raw(raw_record, normalized), reference, {"raw": raw_record})

    def _evidence_from_row(self, row: dict[str, Any], include_details: bool = False) -> EvidenceItem:
        evidence = EvidenceItem(
            evidence_id=row["evidence_id"], research_run_id=row["research_run_id"], source_entity_id=row["source_entity_id"],
            platform=row["platform"], content_type=row["content_type"], content_id=row["content_id"], author_id=row["author_id"],
            source_url=row["source_url"], published_at=row["published_at"], collected_at=row["collected_at"], query_keyword=row["query_keyword"],
            title=row["title"], full_text=row["full_text"], content_hash=row["content_hash"], raw_record_hash=row["raw_record_hash"],
            canonical_content_hash=row["canonical_content_hash"], content_revision_id=row.get("content_revision_id") or f"revision_{self._hash({'source_entity_id': row['source_entity_id'], 'content_hash': row['content_hash'], 'hash_algorithm_version': HASH_ALGORITHM_VERSION})[:24]}",
            hash_algorithm_version=row.get("hash_algorithm_version") or HASH_ALGORITHM_VERSION, engagement=json.loads(row["engagement_json"]),
            raw_record_reference=json.loads(row["raw_record_reference_json"]), metadata=json.loads(row["metadata_json"]),
        )
        if include_details:
            evidence.discovery_contexts = [dict(row) for row in self.storage.list_discovery_context_rows(evidence.evidence_id)]
            evidence.observations = [
                {**dict(row), "engagement": json.loads(row.pop("engagement_json")), "raw_record_reference": json.loads(row.pop("raw_record_reference_json")), "metadata": json.loads(row.pop("metadata_json"))}
                for row in self.storage.list_evidence_observation_rows(evidence.evidence_id)
            ]
        return evidence

    def _summary_from_evidence(self, evidence: EvidenceItem, full_text_limit: int) -> dict[str, Any]:
        full_text = evidence.full_text[:full_text_limit]
        return {
            "evidence_id": evidence.evidence_id,
            "research_run_id": evidence.research_run_id,
            "source_entity_id": evidence.source_entity_id,
            "platform": evidence.platform,
            "content_type": evidence.content_type,
            "content_id": evidence.content_id,
            "author_id": evidence.author_id,
            "source_url": evidence.source_url,
            "published_at": evidence.published_at,
            "collected_at": evidence.collected_at,
            "query_keyword": evidence.query_keyword,
            "title": evidence.title,
            "full_text": full_text,
            "full_text_truncated": len(full_text) < len(evidence.full_text),
            "content_hash": evidence.content_hash,
            "raw_record_hash": evidence.raw_record_hash,
            "canonical_content_hash": evidence.canonical_content_hash,
            "content_revision_id": evidence.content_revision_id,
            "hash_algorithm_version": evidence.hash_algorithm_version,
            "engagement": evidence.engagement,
            "raw_record_reference": evidence.raw_record_reference,
        }

    @staticmethod
    def _dataset_version(dataset_id: str, dataset_dir: Path, duckdb_path: Path) -> str:
        """Fingerprint logical normalized rows plus the raw JSONL inputs they cite."""
        logical: dict[str, Any] = {}
        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            for table in ("contents", "comments"):
                exists = conn.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]).fetchone()[0]
                if not exists:
                    logical[table] = []
                    continue
                cursor = conn.execute(f"SELECT * FROM {table} ORDER BY ALL")
                columns = [item[0] for item in cursor.description]
                logical[table] = [dict(zip(columns, values)) for values in cursor.fetchall()]
        files = []
        raw_dir = dataset_dir / "raw"
        if raw_dir.exists():
            files.extend((path.relative_to(dataset_dir).as_posix(), path) for path in sorted(raw_dir.glob("*.jsonl")))
        payload = {"normalized_logical_rows": logical, "raw_file_hashes": {
            relative_path: hashlib.sha256(path.read_bytes()).hexdigest()
            for relative_path, path in files
        }}
        return f"{dataset_id}:{EvidenceLedger._hash(payload)}"

    @staticmethod
    def _hash(value: Any) -> str:
        return hashlib.sha256(EvidenceLedger._canonical_json(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_json(value: Any) -> str:
        def normalize(item: Any) -> Any:
            if isinstance(item, dict):
                return {unicodedata.normalize("NFC", str(key)): normalize(value) for key, value in item.items()}
            if isinstance(item, list):
                return [normalize(value) for value in item]
            if isinstance(item, str):
                return unicodedata.normalize("NFC", item)
            if isinstance(item, (datetime, date)):
                return item.isoformat()
            return item
        return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _canonical_text(value: str) -> str:
        return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()

    @staticmethod
    def _strings(values: list[str]) -> list[str]:
        return [str(value).strip().lower() for value in values if str(value).strip()]

    @staticmethod
    def _required_id(value: str, label: str) -> str:
        clean = (value or "").strip()
        if not clean:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, f"{label} is required")
        return clean

    @staticmethod
    def _text(value: Any) -> str:
        return "" if value is None else str(value).strip()

    @classmethod
    def _text_or_none(cls, value: Any) -> str | None:
        return cls._text(value) or None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _integer(cls, value: Any) -> int:
        return cls._optional_int(value) or 0

    @classmethod
    def _engagement(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {"like_count": cls._integer(row.get("like_count") or row.get("liked_count")), "comment_count": cls._integer(row.get("comment_count")), "share_count": cls._integer(row.get("share_count")), "collect_count": cls._integer(row.get("collect_count") or row.get("collected_count")), "engagement_count": cls._integer(row.get("engagement_count")), "interaction_field_status": cls._text(row.get("interaction_field_status"))}

    @classmethod
    def _engagement_from_raw(cls, raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        value = cls._engagement(raw)
        if not value["engagement_count"]:
            value["engagement_count"] = value["like_count"] + value["comment_count"] + value["share_count"] + value["collect_count"]
        value["interaction_field_status"] = cls._text(normalized.get("interaction_field_status"))
        return value

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if value is None:
            return None
        try:
            return json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return value

    @classmethod
    def _json_object(cls, value: Any) -> dict[str, Any]:
        parsed = cls._json_value(value)
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _timestamp(value: Any) -> str | None:
        return value.isoformat() if isinstance(value, (datetime, date)) else EvidenceLedger._text_or_none(value)

    @staticmethod
    def _platform_id_field(platform: str) -> str:
        return "note_id" if platform == "xhs" else "aweme_id" if platform == "douyin" else "content_id"

    @classmethod
    def _source_entity_id(
        cls,
        platform: str,
        content_type: str,
        content_id: str,
        raw_record: dict[str, Any],
        parent_content_id: str | None = None,
        parent_comment_id: str | None = None,
    ) -> str:
        identity = {
            "platform": platform,
            "content_type": content_type,
            "content_id": content_id or cls._hash(raw_record),
            "parent_content_id": parent_content_id or "",
            "parent_comment_id": parent_comment_id or "",
        }
        return f"source_{cls._hash(identity)[:24]}"

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    rows.append(parsed)
        return rows

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _dedupe_by_id(values: list[Any], field: str) -> list[Any]:
        return list({getattr(value, field): value for value in values}.values())

    @staticmethod
    def _page_limit(limit: int) -> int:
        return max(1, min(int(limit or DEFAULT_PAGE_LIMIT), MAX_PAGE_LIMIT))

    @staticmethod
    def _research_run_from_row(row: dict[str, Any]) -> ResearchRun:
        return ResearchRun(
            research_run_id=row["research_run_id"], question=row["question"], platforms=json.loads(row["platforms_json"]),
            keywords=json.loads(row["keywords_json"]), time_range=json.loads(row["time_range_json"]) if row["time_range_json"] else None,
            sample_limits=json.loads(row["sample_limits_json"]), created_at=row["created_at"], status=row["status"],
            collection_task_ids=json.loads(row["collection_task_ids_json"]), dataset_version=row["dataset_version"],
            source_dataset_id=row.get("source_dataset_id"), normalization_version=row.get("normalization_version"),
            ledger_schema_version=row.get("ledger_schema_version"), build_started_at=row.get("build_started_at"),
            build_completed_at=row.get("build_completed_at"), build_error=row.get("build_error"),
            hash_algorithm_version=row.get("hash_algorithm_version"),
        )
