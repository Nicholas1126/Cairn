from cairn.dispatcher.prompting import load_prompt, render_prompt


def test_all_templates_have_project_knowledge_placeholder():
    for name in ("bootstrap", "bootstrap_conclude", "reason", "explore", "explore_conclude"):
        tpl = load_prompt("default", name + ".md")
        assert "{project_knowledge}" in tpl, name


def test_empty_project_knowledge_renders_clean():
    tpl = load_prompt("default", "reason.md")
    out = render_prompt(tpl, {
        "graph_yaml": "g", "fact_ids": "[]", "open_intents": "[]",
        "max_intents": "3", "skills": "", "project_knowledge": "",
    })
    assert "{project_knowledge}" not in out
