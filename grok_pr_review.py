#!/usr/bin/env python3
"""Run Grok-powered review on a pull request and post results to GitHub."""

from __future__ import annotations

import json
import os
import re
import sys

import requests

XAI_API_URL = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4.6")
MAX_DIFF_CHARS = 80_000


def github_get(url: str, token: str) -> dict:
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def github_post(url: str, token: str, payload: dict) -> dict:
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_pr_diff(repo: str, pr_number: int, token: str) -> str:
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    pr = github_get(url, token)
    diff_url = pr["diff_url"]
    resp = requests.get(
        diff_url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3.diff"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.text


def ask_grok(diff: str, pr_title: str, pr_body: str) -> dict:
    api_key = os.environ["XAI_API_KEY"]
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[diff truncated for review]"

    system = (
        "You are a security-focused code reviewer for a flight fare Telegram bot repo. "
        "Review pull requests for bugs, secret leaks, unsafe changes, and breaking workflow logic. "
        "Respond ONLY with valid JSON in this schema:\n"
        '{"verdict":"approve"|"request_changes","summary":"one line","findings":["..."],"risks":["..."]}'
    )
    user = (
        f"PR title: {pr_title}\n\n"
        f"PR description:\n{pr_body or '(none)'}\n\n"
        f"Diff:\n```diff\n{diff}\n```"
    )

    resp = requests.post(
        XAI_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        },
        timeout=180,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError(f"Grok returned non-JSON response: {content[:500]}")
    return json.loads(match.group())


def format_review(result: dict) -> str:
    verdict = result.get("verdict", "request_changes").upper()
    lines = [
        "## Grok agent review",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        f"**Summary:** {result.get('summary', 'No summary')}",
        "",
    ]
    findings = result.get("findings") or []
    if findings:
        lines.append("**Findings:**")
        lines.extend(f"- {item}" for item in findings)
        lines.append("")
    risks = result.get("risks") or []
    if risks:
        lines.append("**Risks:**")
        lines.extend(f"- {item}" for item in risks)
        lines.append("")
    lines.append("_Automated review by Grok. Human approval from @Neha-github1125 is still required._")
    return "\n".join(lines)


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = int(os.environ["PR_NUMBER"])
    token = os.environ["GITHUB_TOKEN"]

    if not os.getenv("XAI_API_KEY"):
        msg = (
            "## Grok agent review failed\n\n"
            "**Reason:** `XAI_API_KEY` is not set in GitHub repository secrets.\n\n"
            "**Fix:** Add your xAI API key at "
            "https://console.x.ai → GitHub repo → Settings → Secrets → Actions → "
            "`XAI_API_KEY`, then re-run this check."
        )
        try:
            github_post(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                token,
                {"body": msg},
            )
        except Exception:  # noqa: BLE001
            pass
        print("Missing XAI_API_KEY secret")
        return 1

    pr = github_get(f"https://api.github.com/repos/{repo}/pulls/{pr_number}", token)
    diff = fetch_pr_diff(repo, pr_number, token)
    result = ask_grok(diff, pr["title"], pr.get("body") or "")
    body = format_review(result)

    event = "COMMENT"
    if result.get("verdict") == "approve":
        event = "COMMENT"
    elif result.get("verdict") == "request_changes":
        event = "REQUEST_CHANGES"

    github_post(
        f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews",
        token,
        {"body": body, "event": event},
    )

    print(body)
    if result.get("verdict") == "approve":
        print("Grok review passed.")
        return 0

    print("Grok requested changes.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
