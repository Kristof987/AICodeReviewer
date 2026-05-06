from .helpers import build_file_blocks


def build_refactor_few_shot_prompt(files):
    joined_files = build_file_blocks(files)

    return f"""
You are a senior Python/Django engineer.
Task: produce refactoring and optimization suggestions only.

Use the style from examples.

Example input snippet:
~~~python
def total_price(items):
    s = 0
    for i in items:
        s = s + i.price
    return s
~~~

Example output:
- Opportunity: Use built-in aggregation.
- Why: Improves readability and reduces manual accumulator logic.
- Suggested refactor: `return sum(i.price for i in items)`
- Impact: Lower cognitive load, minor speed improvement.

Example input snippet:
~~~python
if user is not None:
    if user.is_active:
        send_email(user)
~~~

Example output:
- Opportunity: Reduce nested conditionals.
- Why: Flatter control flow is easier to maintain.
- Suggested refactor: `if user and user.is_active: send_email(user)`
- Impact: Better readability.

Now review the changed files and return concise Markdown with:
- Top opportunities
- Why each helps
- Suggested refactor
- Expected impact

Changed files:

{joined_files}
"""
