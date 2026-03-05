# COmmand to run OSSR analysis on a list of repositories and create issues for identified metadata pitfalls.

# Usage: ./ossr_analysis.sh [--dry-run]

OSSR_CONFIG_FILE="assets/ossr_list_url.json"
OSSR_MAIN_OUTPUT_DIR="ossr"
SNAPSHOT_TAG=$(date +%Y%m%d)

echo "Running OSSR analysis with configuration from $OSSR_CONFIG_FILE, outputting to $OSSR_MAIN_OUTPUT_DIR, and using snapshot tag $SNAPSHOT_TAG."
echo "Do you want to use dry run mode? (y/n)"
read -r use_dry_run
if [[ "$use_dry_run" == "y" ]]; then
    echo "Running in dry run mode. No issues will be created."
    DRY_RUN_FLAG="--dry-run"
else
    echo "Running in normal mode. Issues will be created for identified pitfalls."
    DRY_RUN_FLAG=""
fi

# Load list of repositories and custom message from JSON file
uv run sw-metadata-bot run-pipeline \
--input-file $OSSR_CONFIG_FILE \
--run-name $OSSR_MAIN_OUTPUT_DIR \
--snapshot-tag $SNAPSHOT_TAG \
$DRY_RUN_FLAG