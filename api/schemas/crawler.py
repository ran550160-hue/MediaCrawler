# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/schemas/crawler.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

from enum import Enum
from typing import Optional, Literal
from pydantic import BaseModel, Field


MAX_API_LIMIT_COUNT = 10000


class PlatformEnum(str, Enum):
    """Supported media platforms"""
    XHS = "xhs"
    DOUYIN = "dy"
    KUAISHOU = "ks"
    BILIBILI = "bili"
    WEIBO = "wb"
    TIEBA = "tieba"
    ZHIHU = "zhihu"


class LoginTypeEnum(str, Enum):
    """Login method"""
    QRCODE = "qrcode"
    PHONE = "phone"
    COOKIE = "cookie"


class CrawlerTypeEnum(str, Enum):
    """Crawler type"""
    SEARCH = "search"
    DETAIL = "detail"
    CREATOR = "creator"


class SaveDataOptionEnum(str, Enum):
    """Data save option"""
    CSV = "csv"
    DB = "db"
    JSON = "json"
    JSONL = "jsonl"
    SQLITE = "sqlite"
    MONGODB = "mongodb"
    EXCEL = "excel"


class CrawlerStartRequest(BaseModel):
    """Crawler start request"""
    platform: PlatformEnum
    login_type: LoginTypeEnum = LoginTypeEnum.QRCODE
    crawler_type: CrawlerTypeEnum = CrawlerTypeEnum.SEARCH
    keywords: str = ""  # Keywords for search mode
    specified_ids: str = ""  # Post/video ID list for detail mode, comma-separated
    creator_ids: str = ""  # Creator ID list for creator mode, comma-separated
    start_page: int = 1
    enable_comments: bool = True
    enable_sub_comments: bool = False
    save_option: SaveDataOptionEnum = SaveDataOptionEnum.JSONL
    cookies: str = ""
    headless: bool = False
    max_notes_count: Optional[int] = Field(default=None, ge=1, le=MAX_API_LIMIT_COUNT)
    max_comments_count: Optional[int] = Field(default=None, ge=1, le=MAX_API_LIMIT_COUNT)
    enable_cdp_mode: bool = True
    cdp_connect_existing: bool = True
    cdp_debug_port: int = Field(default=9222, ge=1, le=65535)
    save_data_path: str = ""
    enable_ip_proxy: bool = False
    ip_proxy_provider_name: str = "kuaidaili"
    static_proxy_url: str = ""


class DatasetExportRequest(BaseModel):
    """Dataset bundle export request"""
    name: str
    keywords: list[str]
    description: Optional[str] = None
    platform: PlatformEnum = PlatformEnum.XHS
    crawler_type: CrawlerTypeEnum = CrawlerTypeEnum.SEARCH
    data_root: str = "data"
    output_dir: str = "datasets"
    contents_path: Optional[str] = None
    comments_path: Optional[str] = None
    dataset_id: Optional[str] = None


class CDPBrowserStartRequest(BaseModel):
    """CDP browser start request"""
    port: int = Field(default=9222, ge=1, le=65535)
    headless: bool = False
    browser_path: str = ""
    user_data_dir: str = ""
    start_url: str = ""
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class CDPBrowserOpenRequest(BaseModel):
    """Open a URL in the local CDP browser."""
    port: int = Field(default=9222, ge=1, le=65535)
    url: str = "https://www.xiaohongshu.com/explore"


class AgentXHSSearchRequest(BaseModel):
    """Restricted local-agent XHS search request"""
    keywords: list[str]
    max_contents: int = Field(default=20, ge=1, le=200)
    max_comments_per_content: int = Field(default=10, ge=0, le=200)
    include_comments: bool = True
    include_sub_comments: bool = False
    start_page: int = Field(default=1, ge=1, le=100)
    cdp_debug_port: int = Field(default=9222, ge=1, le=65535)
    headless: bool = False
    timeout_seconds: int = Field(default=1800, ge=30, le=21600)
    dataset_name: str = ""
    description: str = ""


class AgentTaskFinalizeRequest(BaseModel):
    """Finalize a local-agent task into a dataset bundle"""
    dataset_name: str = ""
    description: str = ""
    output_dir: str = "datasets"
    dataset_id: Optional[str] = None
    force: bool = False


class CrawlerStatusResponse(BaseModel):
    """Crawler status response"""
    status: Literal["idle", "running", "stopping", "error"]
    platform: Optional[str] = None
    crawler_type: Optional[str] = None
    started_at: Optional[str] = None
    error_message: Optional[str] = None


class LogEntry(BaseModel):
    """Log entry"""
    id: int
    timestamp: str
    level: Literal["info", "warning", "error", "success", "debug"]
    message: str


class DataFileInfo(BaseModel):
    """Data file information"""
    name: str
    path: str
    size: int
    modified_at: str
    record_count: Optional[int] = None
