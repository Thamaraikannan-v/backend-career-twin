# GCP Deployment Setup Guide

## Files Created
- **Dockerfile** - Containerizes your FastAPI app
- **.dockerignore** - Excludes unnecessary files from Docker build
- **.github/workflows/deploy-gcp.yml** - GitHub Actions CI/CD pipeline

## Prerequisites

### 1. GCP Project Setup
```bash
# Create a new GCP project or use existing
gcloud projects create career-twin --name="Career Twin Backend"
gcloud config set project career-twin

# Enable required services
gcloud services enable \
  run.googleapis.com \
  containerregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com
```

### 2. Create Service Account for GitHub Actions

```bash
# Create service account
gcloud iam service-accounts create github-actions \
  --display-name="GitHub Actions"

# Get the service account email
export SA_EMAIL=github-actions@career-twin.iam.gserviceaccount.com

# Grant Cloud Run Deploy permission
gcloud projects add-iam-policy-binding career-twin \
  --member=serviceAccount:$SA_EMAIL \
  --role=roles/run.admin

# Grant Service Account User role
gcloud projects add-iam-policy-binding career-twin \
  --member=serviceAccount:$SA_EMAIL \
  --role=roles/iam.serviceAccountUser

# Grant Container Registry permissions
gcloud projects add-iam-policy-binding career-twin \
  --member=serviceAccount:$SA_EMAIL \
  --role=roles/storage.admin
```

### 3. Set Up Workload Identity Federation (Recommended - No Keys Needed)

```bash
# Enable required service
gcloud services enable iap.googleapis.com

# Create a Workload Identity Pool
gcloud iam workload-identity-pools create github \
  --project=career-twin \
  --location=global \
  --display-name="GitHub Actions"

# Get the Workload Identity Pool ID
export POOL_ID=$(gcloud iam workload-identity-pools describe github \
  --project=career-twin \
  --location=global \
  --format='value(name)')

# Create a Workload Identity Provider
gcloud iam workload-identity-pools providers create-oidc github \
  --project=career-twin \
  --location=global \
  --workload-identity-pool=github \
  --display-name="GitHub" \
  --attribute-mapping="google.subject=assertion.sub,assertion.aud=assertion.aud" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-condition="assertion.repository_owner == 'YOUR_GITHUB_USERNAME'"

# Get the Provider resource name
export PROVIDER=$(gcloud iam workload-identity-pools providers describe github \
  --project=career-twin \
  --location=global \
  --workload-identity-pool=github \
  --format='value(name)')

# Grant Workload Identity User role to the service account
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --project=career-twin \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/$POOL_ID/attribute.repository/YOUR_GITHUB_ORG/career-twin"
```

## GitHub Secrets to Configure

Go to your GitHub repository → Settings → Secrets and Variables → Actions, and add:

```
GCP_PROJECT_ID         = career-twin
WIF_PROVIDER           = projects/YOUR_PROJECT_NUMBER/locations/global/workloadIdentityProviders/YOUR_PROVIDER_ID
WIF_SERVICE_ACCOUNT    = github-actions@career-twin.iam.gserviceaccount.com

# API Keys (from your config.py)
GEMINI_API_KEY         = your_gemini_api_key
GROQ_API_KEY           = your_groq_api_key
SUPABASE_URL           = your_supabase_url
SUPABASE_SERVICE_KEY   = your_supabase_service_key
SUPABASE_JWT_SECRET    = your_supabase_jwt_secret
TAVILY_API_KEY         = your_tavily_api_key
STRIPE_SECRET_KEY      = your_stripe_secret_key
STRIPE_WEBHOOK_SECRET  = your_stripe_webhook_secret
STRIPE_PRO_PRICE_ID    = your_stripe_pro_price_id
HUNTER_API_KEY         = your_hunter_api_key
EXA_API_KEY            = your_exa_api_key
CORS_ORIGINS           = https://yourdomain.com,https://www.yourdomain.com
```

## Deployment

### Option 1: Cloud Run (Recommended - Serverless)
- Scales automatically
- Pay only for what you use
- No container management needed
- Perfect for FastAPI

The workflow automatically deploys to Cloud Run on every push to `main` or `deploy` branch.

### Option 2: GKE (Kubernetes)
If you prefer GKE, you'll need to:
1. Create a GKE cluster
2. Update the workflow to build image and deploy to your cluster
3. Configure ingress for traffic routing

## Monitoring & Logs

```bash
# View Cloud Run logs
gcloud run services describe career-twin-backend --region=us-central1

# Stream logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=career-twin-backend" \
  --region=us-central1 --limit 50 --format json

# View in Cloud Console
# https://console.cloud.google.com/run/detail/us-central1/career-twin-backend
```

## Testing the Deployment

After deployment:

```bash
# Get the service URL
gcloud run services describe career-twin-backend \
  --region=us-central1 \
  --format='value(status.url)'

# Test the API
curl https://career-twin-backend-XXXX.run.app/docs
```

## Environment-Specific Branches

- Push to `main` → Production deployment
- Create `deploy` branch for staging if needed

## Troubleshooting

**Image build fails:**
- Check Docker syntax: `docker build -t test .`
- Ensure all requirements are in requirements.txt

**Deployment fails:**
- Check Cloud Run quotas: `gcloud compute project-info describe --project=career-twin`
- Verify service account permissions
- Check Cloud Build logs in GCP Console

**Application crashes at startup:**
- Check logs: `gcloud logging read` command above
- Verify all environment variables are set
- Test locally: `docker run -p 8080:8080 -e GROQ_API_KEY=xxx IMAGE_NAME`

## Cost Estimates

- **Cloud Run**: ~$0.25/million requests + compute time (~$0.0000417/GB-second)
- **GCP Project Storage**: Free tier includes generous limits
- **Container Registry**: ~$0.10/GB/month for storage
