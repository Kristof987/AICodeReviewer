from .helpers import build_file_blocks


def build_general_minimal_prompt(files):
    joined_files = build_file_blocks(files)

    return f"""
You are a senior software engineer doing a pull request code review.

Review only the changed files below.

Focus on:
- correctness
- bugs
- security
- performance
- maintainability

Return concise Markdown with:
- Summary
- Issues Found
- Suggested Improvements
- Risk Level (Low/Medium/High)

Changed files:

{joined_files}
"""
