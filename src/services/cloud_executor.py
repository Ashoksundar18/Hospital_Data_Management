import os
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError

from src.models.storage_object import StorageClass

logger = logging.getLogger(__name__)


class StorageExecutionError(Exception):
    """Raised when an operation against a cloud storage provider fails."""
    pass


class ObjectNotFoundError(StorageExecutionError):
    """Raised when the target object does not exist in the cloud bucket."""
    pass


# Mapping from domain StorageClass to AWS S3 storage class headers
S3_TIER_MAPPING: Dict[StorageClass, str] = {
    StorageClass.HOT: "STANDARD",
    StorageClass.COOL: "STANDARD_IA",
    StorageClass.COLD: "GLACIER_IR",
    StorageClass.ARCHIVE: "GLACIER",
    StorageClass.DEEP_ARCHIVE: "DEEP_ARCHIVE"
}


class BaseStorageExecutor(ABC):
    """
    Abstract interface for cloud storage lifecycle operations across cloud providers.
    """

    @abstractmethod
    def transition_object(self, bucket: str, key: str, target_class: StorageClass) -> Dict[str, Any]:
        """Transitions an object to a new storage class tier."""
        pass

    @abstractmethod
    def delete_object(self, bucket: str, key: str) -> Dict[str, Any]:
        """Deletes an object from the cloud bucket."""
        pass

    @abstractmethod
    def get_object_metadata(self, bucket: str, key: str) -> Dict[str, Any]:
        """Retrieves metadata and current storage class for an object."""
        pass


class S3StorageExecutor(BaseStorageExecutor):
    """
    AWS S3 implementation of the storage lifecycle executor using boto3.
    Operates with minimal required IAM permissions and never logs credentials.
    """

    def __init__(
        self,
        region_name: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        client: Optional[Any] = None
    ):
        self.region = region_name or os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
        self.endpoint_url = endpoint_url or os.getenv("AWS_ENDPOINT_URL")

        if client:
            self._client = client
        else:
            client_kwargs = {
                "region_name": self.region,
            }
            if self.endpoint_url:
                client_kwargs["endpoint_url"] = self.endpoint_url
            
            # Credentials are read automatically by boto3 from environment variables:
            # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
            self._client = boto3.client("s3", **client_kwargs)

    def __repr__(self) -> str:
        return f"<S3StorageExecutor region={self.region}>"

    def __str__(self) -> str:
        return f"S3StorageExecutor(region={self.region})"

    def get_object_metadata(self, bucket: str, key: str) -> Dict[str, Any]:
        """
        Retrieves head object metadata. Normalizes missing StorageClass header to 'STANDARD'.
        """
        try:
            res = self._client.head_object(Bucket=bucket, Key=key)
            # AWS S3 omits the StorageClass header for STANDARD tier objects
            if "StorageClass" not in res:
                res["StorageClass"] = "STANDARD"
            return res
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                raise ObjectNotFoundError(f"Object '{key}' not found in bucket '{bucket}'") from e
            raise StorageExecutionError(f"Failed to fetch metadata for s3://{bucket}/{key}: {e}") from e

    def transition_object(self, bucket: str, key: str, target_class: StorageClass) -> Dict[str, Any]:
        """
        Transitions object in-place using S3 CopyObject with MetadataDirective='COPY'.
        """
        s3_storage_class = S3_TIER_MAPPING.get(target_class)
        if not s3_storage_class:
            raise StorageExecutionError(f"Unsupported S3 target storage class '{target_class}'")

        # Verify object existence before initiating transition
        self.get_object_metadata(bucket, key)

        try:
            logger.info(f"Transitioning s3://{bucket}/{key} to {s3_storage_class}")
            copy_source = {"Bucket": bucket, "Key": key}
            res = self._client.copy_object(
                Bucket=bucket,
                Key=key,
                CopySource=copy_source,
                StorageClass=s3_storage_class,
                MetadataDirective="COPY"
            )
            return {
                "status": "SUCCESS",
                "action": "TRANSITION",
                "bucket": bucket,
                "key": key,
                "target_storage_class": s3_storage_class,
                "version_id": res.get("VersionId")
            }
        except ClientError as e:
            logger.error(f"S3 transition failed for s3://{bucket}/{key}: {e}")
            raise StorageExecutionError(f"Failed to transition s3://{bucket}/{key} to {s3_storage_class}: {e}") from e

    def delete_object(self, bucket: str, key: str) -> Dict[str, Any]:
        """
        Deletes object from S3 bucket.
        """
        # Verify object exists first to prevent silent phantom deletions
        self.get_object_metadata(bucket, key)

        try:
            logger.info(f"Deleting s3://{bucket}/{key}")
            res = self._client.delete_object(Bucket=bucket, Key=key)
            return {
                "status": "SUCCESS",
                "action": "DELETE",
                "bucket": bucket,
                "key": key,
                "version_id": res.get("VersionId")
            }
        except ClientError as e:
            logger.error(f"S3 deletion failed for s3://{bucket}/{key}: {e}")
            raise StorageExecutionError(f"Failed to delete s3://{bucket}/{key}: {e}") from e
