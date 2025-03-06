import os
import json
import logging
from google.cloud import storage
from src.config.settings import PollConfig

logger = logging.getLogger(__name__)

def load_configs_from_cloud_storage():
    """Load poll configurations from Cloud Storage bucket."""
    configs = []
    try:
        # Get project ID for bucket name construction
        project_id = os.environ.get("GCP_PROJECT_ID")
        
        # Check for bucket name in environment
        bucket_name = os.environ.get("GCP_STORAGE_BUCKET")
        
        # If bucket name not explicitly set, construct it from project ID
        if not bucket_name and project_id:
            bucket_name = f"{project_id}-poll-configs"
            logger.info(f"GCP_STORAGE_BUCKET not set, using constructed name: {bucket_name}")
        
        if not bucket_name:
            logger.warning("GCP_STORAGE_BUCKET and GCP_PROJECT_ID not set, skipping Cloud Storage config loading")
            return configs
            
        # Initialize Cloud Storage client
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        
        # List all JSON files in the bucket
        blobs = bucket.list_blobs(prefix="", delimiter="/")
        
        for blob in blobs:
            if blob.name.endswith(".json"):
                # Download the file contents
                content = blob.download_as_text()
                config = json.loads(content)
                configs.append(config)
                logger.info(f"Loaded poll config from Cloud Storage: {blob.name}")
                
        return configs
    except Exception as e:
        logger.error(f"Error loading configs from Cloud Storage: {e}", exc_info=True)
        return configs
