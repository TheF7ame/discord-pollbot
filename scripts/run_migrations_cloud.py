import os
import asyncio
import subprocess
import re
import tempfile
from dotenv import load_dotenv
from google.cloud import secretmanager

def get_secret(secret_id):
    """Retrieve a secret from Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.environ.get("GCP_PROJECT_ID")
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

def get_redacted_url(url):
    """Redact sensitive information from URL for logging purposes."""
    if not url:
        return "None"
    # Redact password in URL
    redacted = re.sub(r'://([^:]+):([^@]+)@', r'://\1:****@', url)
    return redacted

async def main():
    """Run Alembic migrations against Cloud SQL."""
    load_dotenv()
    
    # Get project ID from environment
    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        print("Error: GCP_PROJECT_ID environment variable not set")
        return
    
    print(f"Setting up for project: {project_id}")
    
    # Get database URL from Secret Manager
    try:
        database_url = get_secret("DATABASE_URL")
        print(f"Retrieved database URL from Secret Manager (credentials redacted): {get_redacted_url(database_url)}")
        
        # Convert Cloud SQL Unix socket URL to localhost TCP connection for local migrations
        # Example: postgresql+asyncpg://user:pass@/dbname?host=/cloudsql/project:region:instance
        # To: postgresql://user:pass@localhost:5433/dbname
        local_database_url = None
        if "/cloudsql/" in database_url:
            # Extract username, password, and database name
            match = re.match(r'postgresql\+asyncpg://([^:]+):([^@]+)@/([^?]+)', database_url)
            if match:
                username, password, dbname = match.groups()
                # Create new URL for local proxy
                # Change to postgresql:// (without asyncpg) for Alembic to work correctly
                local_database_url = f"postgresql://{username}:{password}@localhost:5433/{dbname}"
                print(f"Using local proxy URL (credentials redacted): {get_redacted_url(local_database_url)}")
                database_url = local_database_url
        
        # Temporarily set the DATABASE_URL environment variable for Alembic
        # This is a secure approach - the variable only exists for this process
        original_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        
        print("Testing database connection...")
        try:
            # Test database connection
            test_command = """
import sqlalchemy
from sqlalchemy import text
import sys
try:
    url = os.environ.get('DATABASE_URL')
    engine = sqlalchemy.create_engine(url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).fetchone()
        print(f"Database connection successful")
except Exception as e:
    print(f"Error connecting to database: {e}")
    sys.exit(1)
"""
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write("import os\n" + test_command)
                test_script = f.name
            
            subprocess.run(["python", test_script], check=True)
            os.unlink(test_script)
        except Exception as e:
            print(f"Database connection test failed: {e}")
            # Restore original DATABASE_URL if it existed
            if original_db_url:
                os.environ["DATABASE_URL"] = original_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            return
            
        print("\nRunning Alembic migrations...")
        try:
            # Run Alembic migration using the environment variable
            subprocess.run(["alembic", "upgrade", "head"], check=True)
            print("Migrations completed successfully!")
        finally:
            # Always restore the original DATABASE_URL if it existed
            if original_db_url:
                os.environ["DATABASE_URL"] = original_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            print("Environment restored")
            
    except Exception as e:
        print(f"Error running migrations: {e}")

if __name__ == "__main__":
    asyncio.run(main()) 