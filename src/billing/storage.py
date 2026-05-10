"""Cliente S3/MinIO para operaciones de almacenamiento (Fase 23B).

La función ``get_s3_client`` devuelve un objeto boto3 S3 client.
En tests se mockea este módulo completo para evitar llamadas reales.

Variables de entorno requeridas:
- S3_BUCKET_NAME     — nombre del bucket (default: "chatpro-exports")
- S3_ENDPOINT_URL    — URL del endpoint (para MinIO en dev/test)
- S3_ACCESS_KEY      — access key
- S3_SECRET_KEY      — secret key
- S3_REGION          — región AWS (default: "us-east-1")
"""

from __future__ import annotations

import io
from datetime import timedelta

from src.config import get_settings


def get_s3_client():
    """Crea un cliente boto3 S3 configurado según Settings."""
    import boto3  # import tardío para no romper si boto3 no está instalado en dev

    s = get_settings()
    kwargs: dict = {
        "aws_access_key_id": s.s3_access_key,
        "aws_secret_access_key": s.s3_secret_key,
        "region_name": s.s3_region,
    }
    if s.s3_endpoint_url:
        kwargs["endpoint_url"] = s.s3_endpoint_url

    return boto3.client("s3", **kwargs)


def upload_bytes(data: bytes, key: str) -> str:
    """Sube ``data`` al bucket configurado con ``key`` como path.

    Devuelve la URI completa: s3://{bucket}/{key}.
    """
    s = get_settings()
    client = get_s3_client()
    client.put_object(Bucket=s.s3_bucket_name, Key=key, Body=io.BytesIO(data))
    return f"s3://{s.s3_bucket_name}/{key}"


def generate_presigned_url(key: str, ttl_seconds: int = 900) -> str:
    """Genera una URL firmada (GET) con TTL en segundos (default 15 min)."""
    s = get_settings()
    client = get_s3_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": s.s3_bucket_name, "Key": key},
        ExpiresIn=ttl_seconds,
    )
    return url
