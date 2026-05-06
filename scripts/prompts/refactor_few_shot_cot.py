from .helpers import build_file_blocks


def build_refactor_few_shot_cot_prompt(files):
    joined_files = build_file_blocks(files)

    return f"""
You are a senior Python/Django engineer.
Task: provide refactoring and optimization recommendations only.

Use this process internally:
1) Identify hotspots (duplication, complex branches, unnecessary queries, repeated computation).
2) Compare 1-2 candidate refactors.
3) Choose the safest high-impact option.
4) Output only the final recommendations.

Few-shot example:
Input:
~~~python
for p in projects:
    tasks = Task.objects.filter(project=p)
    total += tasks.count()
~~~

Reasoning pattern (short):
- Hotspot: potential N+1 query.
- Candidate A: prefetch related tasks.
- Candidate B: aggregate with annotation.
- Safer high-impact choice: annotate count once.

Output style:
- Opportunity: Remove N+1 query pattern.
- Why: Reduces DB round trips and latency.
- Suggested refactor: use `annotate(task_count=Count("tasks"))` and sum from annotated rows.
- Impact: Significant performance gain on larger datasets.

Now analyze changed files and return concise Markdown with:
- Top opportunities
- Why each helps
- Suggested refactor
- Expected impact

Do not output private chain-of-thought; output only conclusions.

Changed files:

{joined_files}
"""
