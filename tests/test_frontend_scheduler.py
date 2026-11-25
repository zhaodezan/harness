from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_scheduled_tasks_page_exposes_cron_script_confirmation_and_run_history():
    source = (FRONTEND / "pages" / "ScheduledTasksPage.tsx").read_text(encoding="utf-8")
    assert "cron_expression" in source
    assert "timezone" in source
    assert "script_hash" in source
    assert "确认启用" in source
    assert "立即运行" in source
    assert "标准输出" in source and "错误输出" in source
    assert "非沙箱" in source
    assert "确认当前脚本版本" in source


def test_scheduled_tasks_sidebar_route_is_separate_from_other_workspace_tools():
    source = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "ScheduledTasksPage" in source
    assert 'path="/scheduled-tasks"' in source
    assert "定时任务" in source
    assert "数据库" in source and "知识库" in source and "长期记忆" in source
