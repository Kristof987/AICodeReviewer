from .helpers import build_file_blocks


def build_refactor_minimal_prompt(files):
    joined_files = build_file_blocks(files)

    return f"""
You are a senior Python/Django engineer.

Task: suggest refactoring and code optimization opportunities only.

Rules:
- prioritize low-risk, high-impact improvements
- keep suggestions concrete and actionable
- do not change business behavior

Return Markdown with:
- Top opportunities
- Why each helps
- Suggested refactor
- Expected impact

Changed files:

{joined_files}
"""
