import ast
import os
import re
import json
import sys

from langchain.chains.conversation.base import ConversationChain
from langchain_community.chat_models import ChatOpenAI

current_dir = os.path.dirname(__file__)
root_path = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(root_path)

from consts.commit_message_prompt import COMMIT_MESSAGE_PROMPT
from consts.optimality_prompt import OPTIMALITY_PROMPT
from common.llm_functions import get_llm_response
from consts import decide_review_tasks_prompt
from consts.doc_comment_prompt import DOC_COMMENT_PROMPT
from consts.refactor_prompt import REFACTOR_PROMPT
from consts.static_analysis_prompt import STATIC_ANALYSIS_PROMPT
from consts.security_prompt import SECURITY_PROMPT
from get_pr_comments import get_commit_messages
from pr_inline_comment import comment_on_commit

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def write_number_before_line(code):
    numbered_lines = [
        f"{i + 1}: {line}" for i, line in enumerate(code.splitlines())
    ]
    output = "\n".join(numbered_lines)
    return output

def get_content(file_path):
    with open(file_path, "r") as file:
        content = file.read()
    return content

def get_agent_list(code):
    prompt = decide_review_tasks_prompt.DECIDE_REVIEW_TASKS_PROMPT.format(code=code)
    response = get_llm_response(prompt)

    print(f"Response: {response}")

    match = re.search(r'\[.*\]', response)
    return ast.literal_eval(match.group(0)) if match else []

def parse_llm_commit_feedback(llm_response):
    for line in llm_response.splitlines():
        if line.strip().startswith("The commit messages are good."):
            return line.strip()
        elif line.strip().startswith("The commit messages are not good enough."):
            return line.strip()

def handle_case(prompt_template, numbered_code, changed_file, suffix, conversation):
    prompt = prompt_template.format(numbered_code=numbered_code)
    response = conversation.run(prompt)

    print(f"Response: {response}")

    match = re.search(r'The necessary suggestions are:\s*(\[.*\])', response, re.DOTALL)

    if match:
        suggestions = match.group(1).strip()

        print(f"Suggestions: {suggestions}")

        file_path = f"tmp/{changed_file}_tmp_llm_review_{suffix}.txt"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as file:
            file.write(suggestions)
    else:
        print("No JSON found.")

llm = ChatOpenAI(model_name="gpt-4o-mini", api_key=OPENAI_API_KEY)

conversation = ConversationChain(
    llm=llm
)

def main():
    excluded_files = sys.argv[1] # list of excluded files
    changed_files = sys.argv[2] #list of changed files

    excluded_files_list = excluded_files.split()
    changed_files_list = changed_files.split()

    commit_response = parse_llm_commit_feedback(get_llm_response(COMMIT_MESSAGE_PROMPT.format(commit_messages=get_commit_messages())))
    comment_on_commit(commit_response)

    for changed_file in changed_files_list:
        if changed_file in excluded_files_list:
            print(f"File {changed_file} is excluded from review, moving to next changed file.")
        else:
            print(f"File {changed_file} is included in review, proceeding with review.")

            code = get_content(changed_file)
            agent_list = get_agent_list(code)
            numbered_code = write_number_before_line(code)

            for agent_type in agent_list:

                print(f"Agent type: {agent_type}")
                print(f"Agent list: {agent_list}")

                match agent_type.upper():
                    case "DOC COMMENTS":
                        handle_case(DOC_COMMENT_PROMPT, numbered_code, changed_file, "doc_comment", conversation)

                    case "REFACTOR":
                        handle_case(REFACTOR_PROMPT, numbered_code, changed_file, "refactor", conversation)

                    case "STATIC ANALYSIS":
                        handle_case(STATIC_ANALYSIS_PROMPT, numbered_code, changed_file, "static_analysis",
                                    conversation)
                    case "OPTIMALITY":
                        handle_case(OPTIMALITY_PROMPT, numbered_code, changed_file, "optimality",
                                    conversation)
                    case "SECURITY":
                        handle_case(SECURITY_PROMPT, numbered_code, changed_file, "security", conversation)

if __name__ == "__main__":
    main()