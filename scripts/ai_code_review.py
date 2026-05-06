import os
import subprocess
from pathlib import Path

import requests
from openai import OpenAI


MAX_FILE_CHARS = 12000
ALLOWED_EXTENSIONS = {".py", ".js", ".ts", ".java", ".html", ".css"}


def run_command(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    print("COMMAND:", " ".join(command))
    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)
    print("RETURN CODE:", result.returncode)

    return result.stdout.strip()


def get_changed_files(base_sha, head_sha):
    output = run_command([
        "git",
        "diff",
        "--name-only",
        f"{base_sha}..{head_sha}",
    ])

    files = []

    for file in output.splitlines():
        path = Path(file)

        if path.exists() and path.is_file() and path.suffix in ALLOWED_EXTENSIONS:
            files.append(file)

    return files


def read_file_safely(file_path):
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = Path(file_path).read_text(encoding="latin-1")

    if len(text) > MAX_FILE_CHARS:
        return text[:MAX_FILE_CHARS] + "\n\n... FILE TRUNCATED ..."

    return text


def build_prompt(files):
    file_blocks = []

    for filename, content in files.items():
        file_blocks.append(
            f"""
File: {filename}

~~~text
{content}
~~~
"""
        )

    joined_files = "\n\n".join(file_blocks)

    return f"""
You are a senior software engineer doing a pull request code review.

Review only the changed files below.

Focus on:
- bugs
- security issues
- performance problems
- duplicated code
- bad naming
- missing validation
- refactoring opportunities
- maintainability
- Django/Python best practices if applicable

Return the review in Markdown.

Use this structure:

## AI Code Review

### Summary
Short summary.

### Issues Found
For each issue:
- file name
- problem
- why it matters
- suggested fix

### Suggested Improvements
Concrete refactoring or optimization ideas.

### Risk Level
Low / Medium / High

Changed files:

{joined_files}
"""


def call_openai(prompt):
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    response = client.responses.create(
        model="gpt-5.2",
        input=prompt,
    )

    return response.output_text


def post_pr_comment(comment):
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]

    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"body": comment},
        timeout=30,
    )

    print("GitHub response:", response.status_code, response.text)

    response.raise_for_status()


def main():
    print("AI reviewer script started")

    base_sha = os.environ.get("BASE_SHA")
    head_sha = os.environ.get("HEAD_SHA")
    pr_number = os.environ.get("PR_NUMBER")

    print("BASE_SHA:", base_sha)
    print("HEAD_SHA:", head_sha)
    print("PR_NUMBER:", pr_number)

    changed_files = get_changed_files(base_sha, head_sha)

    print("Changed files:", changed_files)

    if not changed_files:
        post_pr_comment("## AI Code Review\n\nNo reviewable changed source files found.")
        return

    files = {
        file: read_file_safely(file)
        for file in changed_files
    }

    prompt = build_prompt(files)

    print("Prompt length:", len(prompt))

    review = call_openai(prompt)

    print("Review preview:", review[:500])

    post_pr_comment(review)

    print("Comment posted successfully")


if __name__ == "__main__":
    main()