from eda_agent.cli import DEFAULT_PROMPT, load_prompt


def test_default_system_prompt_loads_from_prompts_dir():
    text = load_prompt(DEFAULT_PROMPT)
    assert text.startswith("You are an engineering agent") and "Residual risk" in text


def test_prompt_can_be_a_draft_file(tmp_path):
    draft = tmp_path / "draft.md"
    draft.write_text("You are a test prompt.\n", encoding="utf-8")
    assert load_prompt(str(draft)) == "You are a test prompt."
