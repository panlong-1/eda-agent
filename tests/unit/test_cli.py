from eda_agent.cli import DEFAULT_PROMPT, load_prompt


def test_default_system_prompt_loads_from_prompts_dir():
    text = load_prompt(DEFAULT_PROMPT)
    assert text.startswith("You are an engineering agent") and "Residual risk" in text
