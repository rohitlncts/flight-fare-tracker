#!/bin/bash
# One-time setup: add your xAI API key to GitHub Actions secrets.
set -euo pipefail

REPO="rohitlncts/flight-fare-tracker"

echo "=== Grok PR review setup ==="
echo ""
echo "1. Open https://console.x.ai/team/default/api-keys"
echo "2. Create an API key (starts with xai-...)"
echo ""

if [[ -f .env ]] && grep -q '^XAI_API_KEY=.' .env 2>/dev/null; then
  read -r -p "Use XAI_API_KEY from local .env? [Y/n] " use_env
  if [[ "${use_env:-Y}" =~ ^[Yy]$ ]]; then
    KEY=$(grep '^XAI_API_KEY=' .env | cut -d= -f2-)
  fi
fi

if [[ -z "${KEY:-}" ]]; then
  read -r -s -p "Paste your XAI_API_KEY: " KEY
  echo ""
fi

if [[ -z "$KEY" ]]; then
  echo "Error: empty key"
  exit 1
fi

gh secret set XAI_API_KEY --body "$KEY" --repo "$REPO"
echo "✓ XAI_API_KEY saved to GitHub Secrets"

echo ""
echo "Re-running Grok review on open PRs..."
PR=$(gh pr list --repo "$REPO" --state open --json number --jq '.[0].number' 2>/dev/null || true)
if [[ -n "$PR" && "$PR" != "null" ]]; then
  RUN=$(gh run list --repo "$REPO" --workflow=grok-pr-review.yml --limit 1 --json databaseId --jq '.[0].databaseId')
  if [[ -n "$RUN" && "$RUN" != "null" ]]; then
    gh run rerun "$RUN" --failed --repo "$REPO" 2>/dev/null || \
      gh api "repos/$REPO/actions/workflows/grok-pr-review.yml/dispatches" -f ref=main
    echo "✓ Re-triggered Grok PR Review (check PR #$PR in ~1 min)"
  fi
else
  echo "No open PR — next PR will auto-run Grok review."
fi

echo ""
echo "Done."
