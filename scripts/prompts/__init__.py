from .general_minimal import build_general_minimal_prompt
from .refactor_minimal import build_refactor_minimal_prompt
from .refactor_few_shot import build_refactor_few_shot_prompt
from .refactor_few_shot_cot import build_refactor_few_shot_cot_prompt


PROMPT_BUILDERS = {
    "general_minimal": build_general_minimal_prompt,
    "refactor_minimal": build_refactor_minimal_prompt,
    "refactor_few_shot": build_refactor_few_shot_prompt,
    "refactor_few_shot_cot": build_refactor_few_shot_cot_prompt,
}
