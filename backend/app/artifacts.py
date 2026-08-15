"""产物归档：run 目录打包 zip，按配置上传 RustFS（S3 API）或留本地。"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

from .config import get_settings


def archive_run(work_dir: Path, run_dir_name: str, task_id: str, artifacts_dir: Path) -> str:
    """打包 run 目录为 zip，返回 artifacts_ref（本地路径或 s3://key）。"""
    run_dir = work_dir / "strix_runs" / run_dir_name
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    zip_path = artifacts_dir / f"{task_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if run_dir.is_dir():
            for p in sorted(run_dir.rglob("*")):
                if p.is_file() and p.stat().st_size < 200 * 1024 * 1024:
                    zf.write(p, p.relative_to(run_dir.parent))
        # 附带完整 scan.log，便于排查
        scan_log = work_dir / "scan.log"
        if scan_log.is_file():
            zf.write(scan_log, f"_{scan_log.name}")

    s = get_settings()
    if s.s3_enabled:
        key = f"runs/{task_id}.zip"
        _upload_s3(zip_path, key)
        return f"s3://{s.s3_bucket}/{key}"
    return str(zip_path)


def _s3_client():
    import boto3
    from botocore.client import Config as BotoConfig

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket(retries: int = 10, delay_sec: float = 3.0) -> bool:
    """幂等确保归档桶存在（部署时免手动建桶）。

    RustFS 随 compose 与 api 同时启动，可能短暂未就绪，故带重试；
    失败不影响扫描本身，仅任务归档阶段会报错。
    """
    s = get_settings()
    if not s.s3_enabled:
        return False
    for attempt in range(1, retries + 1):
        try:
            client = _s3_client()
            if s.s3_bucket in [b["Name"] for b in client.list_buckets()["Buckets"]]:
                return True
            client.create_bucket(Bucket=s.s3_bucket)
            return True
        except Exception:
            if attempt == retries:
                return False
            time.sleep(delay_sec)
    return False


def _upload_s3(zip_path: Path, key: str) -> None:
    s = get_settings()
    try:
        _s3_client().upload_file(str(zip_path), s.s3_bucket, key)
    except Exception as e:
        # 启动期建桶失败的兜底：上传时桶仍缺失则现场补建一次
        if "NoSuchBucket" in str(e) and ensure_bucket(retries=3, delay_sec=1.0):
            _s3_client().upload_file(str(zip_path), s.s3_bucket, key)
        else:
            raise


def artifact_response_path(artifacts_ref: str) -> Path | None:
    """本地归档直接返回路径；S3 归档由 API 层生成预签名链接。"""
    if artifacts_ref.startswith("s3://"):
        return None
    p = Path(artifacts_ref)
    return p if p.is_file() else None


def presigned_artifact_url(artifacts_ref: str, expires: int = 3600) -> str | None:
    import boto3
    from botocore.client import Config as BotoConfig

    s = get_settings()
    prefix = f"s3://{s.s3_bucket}/"
    if not artifacts_ref.startswith(prefix):
        return None
    key = artifacts_ref[len(prefix):]
    client = boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": s.s3_bucket, "Key": key}, ExpiresIn=expires
    )
