"""Konfigurasi lokal; tidak membaca atau menyimpan token di file."""

import math
import os
import re
from dataclasses import dataclass, fields
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    graph_version: str = "v26.0"
    app_origin: str = "http://127.0.0.1:8000"
    cookie_secure: bool = False
    session_ttl: int = 1200
    browser_ttl: int = 1800
    max_sessions: int = 100
    max_browser_entries: int = 512
    max_token_entries: int = 512
    cleanup_interval: float = 30
    local_window: float = 60
    local_attempts: int = 3
    global_attempts: int = 12
    max_jobs: int = 2
    operation_timeout: float = 30
    request_timeout: float = 10
    connect_timeout: float = 5
    max_connections: int = 4
    max_keepalive_connections: int = 2
    max_pages: int = 20
    page_size: int = 100
    max_response_bytes: int = 1048576
    max_body_bytes: int = 16384
    max_token_length: int = 8192
    retry_base: float = 0.5
    retry_jitter: float = 0.25
    max_retry_wait: float = 5
    cooldown_seconds: float = 60
    usage_threshold: float = 90
    posts_timeout: float = 60
    posts_step_timeout: float = 12
    posts_step_requests: int = 6
    posts_job_timeout: float = 600
    posts_job_requests: int = 300
    posts_job_steps: int = 200
    content_job_idle_ttl: float = 120
    max_content_jobs: int = 20
    max_content_job_items: int = 20000
    max_content_job_bytes: int = 134217728
    posts_max_pages: int = 50
    story_max_items: int = 5000
    audit_ttl: int = 300
    max_audit_entries: int = 20
    max_audit_items: int = 20000
    max_audit_bytes: int = 67108864
    max_audit_pages: int = 200
    dry_workers: int = 2
    graph_request_concurrency: int = 2
    graph_request_interval: float = 1
    graph_max_interval: float = 10
    dry_max_jobs: int = 100
    dry_max_targets: int = 50000
    dry_max_bytes: int = 134217728
    dry_job_ttl: float = 1200
    dry_max_events: int = 100
    dry_body_bytes: int = 8388608
    checkpoint_max_files: int = 100
    checkpoint_max_file_bytes: int = 67108864
    checkpoint_max_bytes: int = 268435456
    checkpoint_retention: float = 604800

    def __post_init__(self):
        if not re.fullmatch(r"v[1-9][0-9]?\.0", self.graph_version):
            raise ValueError("GRAPH_VERSION harus berformat v26.0.")
        parsed = urlsplit(self.app_origin)
        if (parsed.scheme not in {"http", "https"} or parsed.hostname != "127.0.0.1"
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("APP_ORIGIN harus berupa origin localhost 127.0.0.1 tanpa path.")
        try:
            parsed.port
        except ValueError:
            raise ValueError("Port APP_ORIGIN tidak valid.") from None
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                minimum = 0 if item.name == "retry_jitter" else 0.000001
                if not math.isfinite(value) or value < minimum:
                    raise ValueError("Konfigurasi angka harus positif dan finite.")
        if self.max_keepalive_connections > self.max_connections or self.max_jobs > self.max_connections:
            raise ValueError("Pool harus mencukupi batas pekerjaan/keepalive.")
        if self.usage_threshold > 100 or self.page_size > 100:
            raise ValueError("Ambang penggunaan dan ukuran halaman maksimum 100.")
        for name in ("posts_step_requests", "posts_job_requests", "posts_job_steps", "max_content_jobs", "max_content_job_items", "max_content_job_bytes"):
            if type(getattr(self, name)) is not int:
                raise ValueError("Batas pekerjaan harus bilangan bulat positif.")
        if self.browser_ttl < self.session_ttl or self.max_token_length > self.max_body_bytes:
            raise ValueError("TTL browser/ukuran body harus mencukupi sesi/token.")
        if not 1 <= self.dry_workers <= 8 or not 1 <= self.graph_request_concurrency <= min(8,self.max_connections):
            raise ValueError("Worker/request concurrency melampaui batas lokal.")
        if self.graph_max_interval < self.graph_request_interval:
            raise ValueError("Interval maksimum harus mencukupi interval awal.")
        for name in ("dry_workers","graph_request_concurrency","dry_max_jobs","dry_max_targets","dry_max_bytes",
                     "dry_max_events","dry_body_bytes","max_audit_pages","checkpoint_max_files","checkpoint_max_file_bytes","checkpoint_max_bytes"):
            if type(getattr(self,name)) is not int:
                raise ValueError("Batas antrean harus bilangan bulat positif.")
        if self.cookie_secure and parsed.scheme != "https":
            raise ValueError("COOKIE_SECURE membutuhkan APP_ORIGIN HTTPS.")

    @classmethod
    def from_env(cls):
        values = {}
        for item in fields(cls):
            raw = os.environ.get(item.name.upper())
            if raw is None:
                continue
            default = item.default
            try:
                if isinstance(default, bool):
                    if raw.lower() not in {"true", "false", "1", "0"}:
                        raise ValueError()
                    values[item.name] = raw.lower() in {"true", "1"}
                else:
                    values[item.name] = type(default)(raw)
            except ValueError:
                raise ValueError("Konfigurasi " + item.name.upper() + " tidak valid.") from None
        return cls(**values)
