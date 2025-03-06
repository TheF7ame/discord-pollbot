# Discord Poll Bot Deployment Guide

## 1. Install Google Cloud SDK

```bash
# macOS
brew install --cask google-cloud-sdk

# Windows
# Download installer from: https://cloud.google.com/sdk/docs/install

# Linux (Debian/Ubuntu)
sudo apt-get update && sudo apt-get install apt-transport-https ca-certificates gnupg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key --keyring /usr/share/keyrings/cloud.google.gpg add -
sudo apt-get update && sudo apt-get install google-cloud-sdk
```

## 2. Authenticate and Set Project

```bash
# Log in to your Google account
gcloud auth login

# Set your active project
gcloud config set project YOUR_PROJECT_ID

# Verify your current project
gcloud config get-value project
# Should output: YOUR_PROJECT_ID
```

## 3. Enable Required APIs

```bash
gcloud services enable cloudbuild.googleapis.com run.googleapis.com secretmanager.googleapis.com sqladmin.googleapis.com storage.googleapis.com
```

## 4. Create Cloud Storage Bucket

```bash
gsutil mb -l us-central1 gs://YOUR_PROJECT_ID-poll-configs
```

## 5. Upload Poll Configurations

```bash
gsutil cp scripts/*.json gs://YOUR_PROJECT_ID-poll-configs/
```

## 6. Create Cloud SQL Instance

```bash
gcloud sql instances create discord-poll-db \
  --database-version=POSTGRES_14 \
  --tier=db-f1-micro \
  --region=us-central1 \
  --root-password=YOUR_SECURE_PASSWORD
```

## 7. Create Database and User

```bash
# Create database
gcloud sql databases create discordpoll --instance=discord-poll-db

# Create database user
gcloud sql users create discordu \
  --instance=discord-poll-db \
  --password=YOUR_USER_PASSWORD
```

## 8. Store Secrets

```bash
# Store Discord token
echo -n "YOUR_DISCORD_TOKEN" | gcloud secrets create discord-token --data-file=-

# Store Discord application ID
echo -n "YOUR_DISCORD_APPLICATION_ID" | gcloud secrets create discord-application-id --data-file=-

# Store database connection string
echo -n "postgresql+asyncpg://discordu:YOUR_USER_PASSWORD@/discordpoll?host=/cloudsql/YOUR_PROJECT_ID:us-central1:discord-poll-db" | \
  gcloud secrets create database-url --data-file=-
```

## 9. Configure IAM Roles and Permissions

The Discord Poll Bot requires specific IAM roles for the service accounts to access resources such as Cloud Run, Cloud Build, and Secret Manager.

First, retrieve your project number and set up variables for service accounts:

```bash
# Get your project number
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)")
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

# Verify the service accounts
echo "Compute Engine SA: $COMPUTE_SA"
echo "Cloud Build SA: $CLOUDBUILD_SA"
```

Check existing permissions for these service accounts:

```bash
# Check current IAM roles for service accounts
gcloud projects get-iam-policy YOUR_PROJECT_ID --format=json | grep -A 10 "$COMPUTE_SA"
gcloud projects get-iam-policy YOUR_PROJECT_ID --format=json | grep -A 10 "$CLOUDBUILD_SA"
```

Add the necessary permissions if not already present:

```bash
# Grant Secret Manager Secret Accessor role to Compute Engine default service account
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$COMPUTE_SA" \
  --role="roles/secretmanager.secretAccessor"

# Grant Secret Manager Secret Accessor role to Cloud Build service account
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$CLOUDBUILD_SA" \
  --role="roles/secretmanager.secretAccessor"

# Grant Cloud Run Admin role to Cloud Build service account
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$CLOUDBUILD_SA" \
  --role="roles/run.admin"

# Grant Service Account User role to Cloud Build service account
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$CLOUDBUILD_SA" \
  --role="roles/iam.serviceAccountUser"
```

You might also need to check and add roles to your own user account:

```bash
# List your current IAM roles
gcloud projects get-iam-policy YOUR_PROJECT_ID --format=json | grep -A 5 "$(gcloud config get-value account)"

# Add missing roles to your account if needed
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="user:YOUR_EMAIL" \
  --role="roles/cloudbuild.builds.editor"
```

> **Important**: Replace `YOUR_PROJECT_ID` with your Google Cloud project ID and `YOUR_EMAIL` with your Google account email address.

Without these permissions, your deployment will fail with errors such as:
- "Permission denied on secret: projects/YOUR_PROJECT_ID/secrets/DISCORD_TOKEN/versions/latest"
- "The service account used must be granted the 'Secret Manager Secret Accessor' role"

## 10. Deploy to Cloud Run

### Create your cloudbuild.yaml file

The repository includes a `cloudbuild.yaml.example` file with placeholders. You need to create your own `cloudbuild.yaml` file based on this example with your specific values:

1. Copy the example file to create your actual configuration:
   ```bash
   cp cloudbuild.yaml.example cloudbuild.yaml
   ```

2. Edit the `cloudbuild.yaml` file to replace placeholder values:
   - Replace `YOUR_IMAGE_NAME` with `discord-poll-bot` (or your preferred service name)
   - Replace `YOUR_CLOUD_SQL_REGION` with your desired region (e.g., `us-central1`)
   - Replace `YOUR_CLOUD_SQL_INSTANCE_NAME` with your Cloud SQL instance name
   - Replace secret names with your actual secret names if they differ from the defaults
   - Make any other adjustments needed for your specific setup

> **Important**: The `cloudbuild.yaml` file contains sensitive information and should not be committed to your repository. Add it to your `.gitignore` file to prevent accidental commits.

### Submit the build

Once your `cloudbuild.yaml` file is configured:

```bash
gcloud builds submit --config cloudbuild.yaml
```

## 11. Run Initial Database Migrations

```bash
# Download Cloud SQL Auth Proxy
curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.6.0/cloud-sql-proxy.linux.amd64
chmod +x cloud-sql-proxy

# Start the proxy
./cloud-sql-proxy --port 5432 YOUR_PROJECT_ID:us-central1:YOUR_SQL_INSTANCE &
```

### Troubleshooting Cloud SQL Proxy

If you encounter issues with the Cloud SQL Proxy, try these steps:

```bash
# Check if any Cloud SQL Proxy processes are already running
ps aux | grep cloud-sql-proxy

# Kill any existing Cloud SQL Proxy processes if necessary
pkill -f cloud-sql-proxy

# If port 5432 is already in use (e.g., by a local PostgreSQL instance), use a different port
./cloud-sql-proxy --port 5433 YOUR_PROJECT_ID:us-central1:YOUR_SQL_INSTANCE &

# Run the proxy with output redirected to a log file for better monitoring
nohup ./cloud-sql-proxy --port 5433 YOUR_PROJECT_ID:us-central1:YOUR_SQL_INSTANCE > proxy.log 2>&1 &
```

### Install Required Google Cloud Packages

Ensure you have the required Google Cloud packages installed in your Python environment:

```bash
pip install google-cloud-secret-manager google-cloud-storage
```

### Run Migrations

```bash
# Set environment variables
export GCP_PROJECT_ID=YOUR_PROJECT_ID

# Run migrations
python -m scripts.run_migrations_cloud
```

> **Note:** While the DATABASE_URL would typically be required for direct database access, `run_migrations_cloud.py` retrieves this value directly from Secret Manager using the `GCP_PROJECT_ID`, so you don't need to set it manually.

## 12. Monitor Your Deployment

```bash
# View logs from Cloud Run
gcloud run services logs read discord-poll-bot

# Check service status
gcloud run services describe discord-poll-bot

# Access your service at the provided URL (example)
# https://discord-poll-bot-YOUR_PROJECT_NUMBER.us-central1.run.app
```

## Storage Analysis

### Current Parameters
- 10,000 weekly active users
- Multiple tables storing poll data, user votes, scores, and UI states
- Running for 1.5 years (approximately 78 weeks)

### Estimated Weekly Data Generation
- Polls and options: ~20 KB/week
- User votes and selections: ~4 MB/week
- Score updates: ~1 MB/week
- UI states: ~3 MB/week
- Misc. data: ~0.2 MB/week

### Total Estimated Storage
- Weekly growth: ~8 MB
- 1.5 years (78 weeks): ~624 MB
- With indices, logs, and overhead: ~1-1.5 GB

### Cloud SQL db-f1-micro Analysis
- 10 GB storage included
- Automatic storage increases available
- For your estimated 1-1.5 GB requirement over 1.5 years, this tier is sufficient
- You have ~6-7x capacity buffer before needing expansion

### Auto-scaling Capabilities
- **Storage**: Yes, Cloud SQL automatically increases storage when you reach 90% capacity
- **Performance**: To scale compute resources (CPU/RAM), you would need to manually change to a higher tier 

## Local Development with Cloud SQL

### Connecting to Cloud SQL with Postico 2

You can connect to your Cloud SQL instance from local PostgreSQL clients like Postico 2 using the Cloud SQL Auth Proxy:

1. **Ensure Cloud SQL Auth Proxy is running**:
   ```bash
   # Start the proxy if not already running
   ./cloud-sql-proxy --port 5433 YOUR_PROJECT_ID:us-central1:discord-poll-db &
   
   # Or check if it's already running
   ps aux | grep cloud-sql-proxy
   ```

2. **Configure Postico 2 connection**:
   
   In Postico 2, create a new connection with these settings:
   
   - **Nickname**: Cloud SQL Discord Poll Bot
   - **Host**: localhost
   - **Port**: 5433 (or whatever port you specified when starting the proxy)
   - **User**: discorduser (the database user you created)
   - **Password**: YOUR_USER_PASSWORD (the password you set)
   - **Database**: discordpoll
   
   ![Postico Connection Settings](https://i.imgur.com/example.png)

3. **Test connection**:
   
   Click "Connect" to test the connection. If successful, you can now browse and manage your Cloud SQL database as if it were running locally.

4. **When finished**:
   
   You can stop the Cloud SQL Auth Proxy when no longer needed:
   ```bash
   pkill -f cloud-sql-proxy
   ```

> **Note**: Always ensure the Cloud SQL Auth Proxy is running before attempting to connect with Postico 2. 