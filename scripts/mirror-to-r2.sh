#!/bin/bash
# Copy the files of a release to the download mirror, a public Cloudflare R2 bucket (run by the release workflows).
#
#   scripts/mirror-to-r2.sh TAG FILE...
#   scripts/mirror-to-r2.sh --prune KEEP
#
# The files go to <bucket>/<TAG>/<name>; an existing object of the same name is replaced.
# --prune keeps the KEEP newest versions (folders v<version>, compared as versions) and deletes the other v* folders;
# it prints the removed tags, one per line. Needs the AWS CLI and:
#   R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET   the bucket and an API token with write access to it
#   R2_PUBLIC_URL   the bucket's public address (its custom domain or https://pub-….r2.dev), without the tag
# Without --prune it prints the public address of the folder: R2_PUBLIC_URL/TAG.
set -euo pipefail

tag=${1:?usage: mirror-to-r2.sh TAG FILE... | mirror-to-r2.sh --prune KEEP}
shift
[ $# -gt 0 ] || { echo "mirror-to-r2: no files" >&2; exit 1; }
: "${R2_ACCOUNT_ID:?}" "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}" "${R2_BUCKET:?}" "${R2_PUBLIC_URL:?}"

export AWS_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY AWS_DEFAULT_REGION=auto
# R2 rejects the default integrity checksums of AWS CLI 2.23 and later
export AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
endpoint="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

if [ "$tag" = --prune ]; then
  keep=$1
  [[ $keep =~ ^[1-9][0-9]*$ ]] || { echo "mirror-to-r2: KEEP must be a positive number" >&2; exit 1; }
  # "                           PRE v1.10.21/" for every folder of the bucket
  tags=$(aws s3 ls --endpoint-url "$endpoint" "s3://${R2_BUCKET}/" | awk '$1 == "PRE" { sub("/$", "", $2); print $2 }' | grep -E '^v[0-9]' | sort -V || true)
  old=$(printf '%s\n' "$tags" | grep . | head -n "-$keep" || true)
  for t in $old; do
    aws s3 rm --endpoint-url "$endpoint" --recursive --only-show-errors "s3://${R2_BUCKET}/${t}/" >&2
    echo "$t"
  done
  exit 0
fi

for f in "$@"; do
  aws s3 cp --endpoint-url "$endpoint" --no-progress "$f" "s3://${R2_BUCKET}/${tag}/$(basename "$f")" >&2
done
echo "${R2_PUBLIC_URL%/}/${tag}"
