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