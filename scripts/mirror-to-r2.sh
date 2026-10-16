#!/bin/bash
# Copy the files of a release to the download mirror, a public Cloudflare R2 bucket (run by the release workflows).
#
#   scripts/mirror-to-r2.sh TAG FILE...
#
# The files go to <bucket>/<TAG>/<name>; an existing object of the same name is replaced. Needs the AWS CLI and:
#   R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET   the bucket and an API token with write access to it
#   R2_PUBLIC_URL   the bucket's public address (its custom domain or https://pub-….r2.dev), without the tag
# Prints the public address of the folder: R2_PUBLIC_URL/TAG.
set -euo pipefail

tag=${1:?usage: mirror-to-r2.sh TAG FILE...}
shift
[ $# -gt 0 ] || { echo "mirror-to-r2: no files" >&2; exit 1; }
: "${R2_ACCOUNT_ID:?}" "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}" "${R2_BUCKET:?}" "${R2_PUBLIC_URL:?}"

export AWS_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY AWS_DEFAULT_REGION=auto
# R2 rejects the default integrity checksums of AWS CLI 2.23 and later
export AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
endpoint="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

for f in "$@"; do
  aws s3 cp --endpoint-url "$endpoint" --no-progress "$f" "s3://${R2_BUCKET}/${tag}/$(basename "$f")" >&2
done
echo "${R2_PUBLIC_URL%/}/${tag}"
