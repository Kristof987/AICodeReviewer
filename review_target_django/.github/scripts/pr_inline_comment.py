import os
import glob
import json
import re
from github import Github

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY")
PR_NUMBER = os.getenv("GITHUB_EVENT_NUMBER")

print("token: ")
print(GITHUB_TOKEN)

pattern = os.path.join("tmp", "*_tmp_llm_review*.txt")

g = Github(GITHUB_TOKEN)
repo = g.get_repo(GITHUB_REPO)
pr = repo.get_pull(int(PR_NUMBER))
commit = repo.get_commit(pr.head.sha)

suffix_array = [
    "_tmp_llm_review_doc_comment",
    "_tmp_llm_review_static_analysis",
    "_tmp_llm_review_refactor"
    "_tmp_llm_review_optimality",
    "_tmp_llm_review_security",
]


def comment_on_pr(suffixes):
    for root, dirs, files in os.walk("tmp"):
        for file in files:
            if any(file.endswith(suffix + ".txt") for suffix in suffixes):
                full_path = os.path.join(root, file)

                with open(full_path, "r") as file:
                    content = file.read()

                    json_data = json.loads(content)

                    match = re.search(r'^tmp/(.*)_tmp_llm_review_.*\.txt$', file.name)

                    for entry in json_data:
                        line = entry.get("line")
                        comment = entry.get("comment")
                        print(f"  Line {line}: {comment}")

                        pr.create_review_comment(
                            body=comment,
                            commit=commit,
                            path=match.group(1),
                            line=line
                        )


def comment_on_commit(content):
    print(f"Commit status: {content}")

    pr.create_review_comment(
        body=content,
        commit=commit,
        path="",
        line=0
    )

def get_commit_messages():
    commits_array = []

    for act_commit in pr.get_commits():
        commits_array.append(act_commit.commit.message)

    print(commits_array)

    return "\n".join(commits_array)


comment_on_pr(suffix_array)
