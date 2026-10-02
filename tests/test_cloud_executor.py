import os
import pytest
import boto3
from moto import mock_aws

from src.models.storage_object import StorageClass
from src.services.cloud_executor import (
    S3StorageExecutor,
    StorageExecutionError,
    ObjectNotFoundError,
    S3_TIER_MAPPING
)


@pytest.fixture
def aws_credentials(monkeypatch):
    """Mocked AWS Credentials for moto."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing-mock-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing-mock-secret-key")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing-mock-token")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing-mock-session-token")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def s3_setup(aws_credentials):
    """Sets up a moto mocked S3 bucket and sample object."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket_name = "hospital-imaging-archive"
        s3.create_bucket(Bucket=bucket_name)

        # Upload a test DICOM object in STANDARD tier
        object_key = "radiology/patient-9821/mri_scan.dcm"
        s3.put_object(
            Bucket=bucket_name,
            Key=object_key,
            Body=b"MOCK_DICOM_DATA_BYTES",
            StorageClass="STANDARD"
        )
        yield {
            "s3_client": s3,
            "bucket": bucket_name,
            "key": object_key
        }


def test_s3_executor_transition_object(s3_setup):
    """
    Verifies that S3StorageExecutor transitions an object's storage class in-place
    (e.g., from STANDARD to STANDARD_IA and GLACIER).
    """
    bucket = s3_setup["bucket"]
    key = s3_setup["key"]
    executor = S3StorageExecutor(region_name="us-east-1")

    # 1. Transition to COOL (STANDARD_IA)
    result_cool = executor.transition_object(bucket=bucket, key=key, target_class=StorageClass.COOL)
    assert result_cool["status"] == "SUCCESS"
    assert result_cool["target_storage_class"] == "STANDARD_IA"

    meta_cool = executor.get_object_metadata(bucket=bucket, key=key)
    assert meta_cool["StorageClass"] == "STANDARD_IA"

    # 2. Transition to ARCHIVE (GLACIER)
    result_archive = executor.transition_object(bucket=bucket, key=key, target_class=StorageClass.ARCHIVE)
    assert result_archive["status"] == "SUCCESS"
    assert result_archive["target_storage_class"] == "GLACIER"

    meta_archive = executor.get_object_metadata(bucket=bucket, key=key)
    assert meta_archive["StorageClass"] == "GLACIER"


def test_s3_executor_delete_object(s3_setup):
    """
    Verifies that S3StorageExecutor safely deletes an object when requested.
    """
    bucket = s3_setup["bucket"]
    key = s3_setup["key"]
    executor = S3StorageExecutor(region_name="us-east-1")

    # Confirm object exists prior to deletion
    meta = executor.get_object_metadata(bucket=bucket, key=key)
    assert meta is not None

    # Delete object
    result_delete = executor.delete_object(bucket=bucket, key=key)
    assert result_delete["status"] == "SUCCESS"
    assert result_delete["action"] == "DELETE"

    # Verifying object no longer exists raises ObjectNotFoundError
    with pytest.raises(ObjectNotFoundError):
        executor.get_object_metadata(bucket=bucket, key=key)


def test_s3_executor_nonexistent_object(s3_setup):
    """
    Verifies that attempting to transition a missing object raises ObjectNotFoundError.
    """
    bucket = s3_setup["bucket"]
    missing_key = "nonexistent/missing_file.dcm"
    executor = S3StorageExecutor(region_name="us-east-1")

    with pytest.raises(ObjectNotFoundError):
        executor.transition_object(bucket=bucket, key=missing_key, target_class=StorageClass.COOL)


def test_s3_executor_credentials_not_logged(s3_setup, monkeypatch):
    """
    Verifies that cloud executor never leaks secrets or keys in string representations.
    """
    secret = "SUPER_SECRET_AWS_KEY_999"
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)
    executor = S3StorageExecutor(region_name="us-east-1")

    repr_str = repr(executor)
    str_val = str(executor)

    assert secret not in repr_str
    assert secret not in str_val
