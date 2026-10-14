import os
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from flask import current_app

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None


def _azure_client():
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if conn:
        return BlobServiceClient.from_connection_string(conn)
    account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
    if not account_url:
        return None
    return BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())


def _s3_client():
    if boto3 is None:
        raise RuntimeError("boto3 is not installed. Run: pip install -r requirements.txt")
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "ap-south-1"),
        endpoint_url=os.getenv("AWS_S3_ENDPOINT_URL") or None,
    )


def storage_mode():
    return os.getenv("STORAGE_BACKEND", "azure").lower()


def _azure_container():
    client = _azure_client()
    if not client:
        raise RuntimeError("Azure Blob is not configured")
    container = client.get_container_client(current_app.config["AZURE_STORAGE_CONTAINER"])
    try:
        container.create_container()
    except Exception:
        pass
    return container


def _s3_bucket():
    bucket = os.getenv("AWS_S3_BUCKET")
    if not bucket:
        raise RuntimeError("AWS_S3_BUCKET is not configured")
    return bucket


def upload_bytes(blob_name, data, content_type):
    mode = storage_mode()
    errors = []

    if mode in {"azure", "both"}:
        try:
            blob = _azure_container().get_blob_client(blob_name)
            blob.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
        except Exception as exc:
            errors.append(f"Azure Blob: {exc}")

    if mode in {"s3", "both"}:
        try:
            _s3_client().put_object(
                Bucket=_s3_bucket(),
                Key=blob_name,
                Body=data,
                ContentType=content_type,
            )
        except Exception as exc:
            errors.append(f"AWS S3: {exc}")

    if errors:
        raise RuntimeError("; ".join(errors))
    return blob_name


def download_bytes(blob_name):
    mode = storage_mode()
    errors = []

    if mode in {"azure", "both"}:
        try:
            return _azure_container().get_blob_client(blob_name).download_blob().readall()
        except Exception as exc:
            errors.append(f"Azure Blob: {exc}")

    if mode in {"s3", "both"}:
        try:
            obj = _s3_client().get_object(Bucket=_s3_bucket(), Key=blob_name)
            return obj["Body"].read()
        except Exception as exc:
            errors.append(f"AWS S3: {exc}")

    raise RuntimeError("Unable to download object: " + "; ".join(errors))


def delete_bytes(blob_name):
    mode = storage_mode()
    errors = []
    if mode in {"azure", "both"}:
        try:
            _azure_container().delete_blob(blob_name, delete_snapshots="include")
        except Exception as exc:
            errors.append(f"Azure Blob: {exc}")
    if mode in {"s3", "both"}:
        try:
            _s3_client().delete_object(Bucket=_s3_bucket(), Key=blob_name)
        except Exception as exc:
            errors.append(f"AWS S3: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
