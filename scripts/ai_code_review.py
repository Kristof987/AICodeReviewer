import os
import subprocess
from pathlib import Path

import requests
from openai import OpenAI
from prompts import PROMPT_BUILDERS


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


def build_prompt(files, prompt_profile):
    if prompt_profile not in PROMPT_BUILDERS:
        available = ", ".join(PROMPT_BUILDERS.keys())
        raise ValueError(f"Invalid PROMPT_PROFILE: {prompt_profile}. Available: {available}")

    return PROMPT_BUILDERS[prompt_profile](files)


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


def build_comment_with_metadata(review_body, prompt_profile):
    return (
        "## AI Code Review\n\n"
        f"**Prompt profile:** `{prompt_profile}`\n\n"
        f"{review_body}"
    )


def main():
    print("AI reviewer script started")

    base_sha = os.environ.get("BASE_SHA")
    head_sha = os.environ.get("HEAD_SHA")
    pr_number = os.environ.get("PR_NUMBER")
    prompt_profile = os.environ.get("PROMPT_PROFILE", "general_minimal")

    print("BASE_SHA:", base_sha)
    print("HEAD_SHA:", head_sha)
    print("PR_NUMBER:", pr_number)
    print("PROMPT_PROFILE:", prompt_profile)

    changed_files = get_changed_files(base_sha, head_sha)

    print("Changed files:", changed_files)

    if not changed_files:
        post_pr_comment(
            build_comment_with_metadata(
                "No reviewable changed source files found.",
                prompt_profile,
            )
        )
        return

    files = {
        file: read_file_safely(file)
        for file in changed_files
    }

    prompt = build_prompt(files, prompt_profile)

    print("Prompt length:", len(prompt))

    review = call_openai(prompt)

    print("Review preview:", review[:500])

    post_pr_comment(build_comment_with_metadata(review, prompt_profile))

    print("Comment posted successfully")


if __name__ == "__main__":
    main()
