# -*- coding: utf-8 -*-
from pathlib import Path

from fastapi import APIRouter, HTTPException

from mediacrawler_mcp.dataset_bundle_exporter import DatasetBundleExporter
from mediacrawler_mcp.errors import McpAppError

from ..schemas import DatasetExportRequest, PlatformEnum

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post("/export")
async def export_dataset_bundle(request: DatasetExportRequest):
    """Export local crawler output into a dataset bundle."""
    if request.platform != PlatformEnum.XHS:
        raise HTTPException(
            status_code=400,
            detail="Dataset export currently supports platform=xhs only",
        )

    try:
        result = DatasetBundleExporter().export_xhs_bundle(
            name=request.name,
            output_dir=Path(request.output_dir),
            keywords=request.keywords,
            description=request.description,
            data_root=Path(request.data_root),
            crawler_type=request.crawler_type.value,
            contents_path=Path(request.contents_path) if request.contents_path else None,
            comments_path=Path(request.comments_path) if request.comments_path else None,
            dataset_id=request.dataset_id,
        )
    except McpAppError as exc:
        raise HTTPException(status_code=400, detail=exc.to_result()) from exc

    return {"status": "success", **result}
