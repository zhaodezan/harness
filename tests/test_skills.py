from backend.app.skills import discover_skills


def test_discovers_skills_and_loads_instructions_on_demand(tmp_path):
    skill_dir = tmp_path / "summarize"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: summarize\ndescription: Summarize documents\n---\n\nUse concise bullets.",
        encoding="utf-8",
    )

    skills = discover_skills(tmp_path)

    assert len(skills) == 1
    assert skills[0].name == "summarize"
    assert skills[0].description == "Summarize documents"
    assert "content" not in skills[0].to_dict()
    assert skills[0].load_instructions() == "Use concise bullets."


def test_ignores_invalid_skill_and_skill_path_cannot_escape_directory(tmp_path):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "SKILL.md").write_text("no metadata", encoding="utf-8")

    assert discover_skills(tmp_path) == []
