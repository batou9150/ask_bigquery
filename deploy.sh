#!/usr/bin/env bash
# Deploy ask-bigquery to Cloud Run as a private service (IAM-authenticated),
# running as a dedicated service account with read-only access to the allowlisted datasets.
#
#   PROJECT_ID=my-project BQ_ALLOWED_DATASETS=my-project.sales,bigquery-public-data.thelook_ecommerce ./deploy.sh
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID (the project that runs the service and pays for queries)}"
: "${BQ_ALLOWED_DATASETS:?set BQ_ALLOWED_DATASETS (comma-separated project.dataset)}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-ask-bigquery}"
BQ_LOCATION="${BQ_LOCATION:-US}"
BQ_MAX_BYTES_BILLED="${BQ_MAX_BYTES_BILLED:-1000000000}"
SA_EMAIL="${SERVICE}-mcp@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> Enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com bigquery.googleapis.com --project "$PROJECT_ID"

echo "==> Service account $SA_EMAIL"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SERVICE}-mcp" --project "$PROJECT_ID" \
    --display-name "ask-bigquery MCP server (read-only BigQuery)"
fi

# Run query jobs in the billing project. This role does not grant access to any data.
gcloud projects add-iam-policy-binding "$PROJECT_ID" --condition=None --quiet \
  --member "serviceAccount:$SA_EMAIL" --role roles/bigquery.jobUser >/dev/null

# Read the allowlisted datasets, and nothing else. Public datasets need no grant.
IFS=',' read -ra DATASETS <<<"$BQ_ALLOWED_DATASETS"
for dataset in "${DATASETS[@]}"; do
  if [[ "$dataset" == bigquery-public-data.* ]]; then
    echo "    $dataset: public dataset, no grant needed"
  else
    echo "    $dataset: granting roles/bigquery.dataViewer"
    bq add-iam-policy-binding --dataset \
      --member "serviceAccount:$SA_EMAIL" --role roles/bigquery.dataViewer \
      "${dataset/./:}" >/dev/null
  fi
done

echo "==> Deploying $SERVICE to Cloud Run ($REGION)"
# `^;^` makes `;` the separator, since BQ_ALLOWED_DATASETS contains commas.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --source . --quiet \
  --service-account "$SA_EMAIL" \
  --no-allow-unauthenticated \
  --max-instances 3 --memory 512Mi --cpu 1 --timeout 300 \
  --set-env-vars "^;^BQ_BILLING_PROJECT=$PROJECT_ID;BQ_ALLOWED_DATASETS=$BQ_ALLOWED_DATASETS;BQ_LOCATION=$BQ_LOCATION;BQ_MAX_BYTES_BILLED=$BQ_MAX_BYTES_BILLED"

cat <<DONE

Deployed. The service is private: callers need roles/run.invoker, e.g.
  gcloud run services add-iam-policy-binding $SERVICE --region $REGION --project $PROJECT_ID \\
    --member user:you@example.com --role roles/run.invoker

Then open an authenticated tunnel from your machine:
  gcloud run services proxy $SERVICE --region $REGION --project $PROJECT_ID --port 3000

and point your MCP client at http://localhost:3000/mcp, e.g.
  claude mcp add --transport http bigquery http://localhost:3000/mcp
DONE
