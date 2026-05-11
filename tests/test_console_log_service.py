from services.model_update.analysis import console_log_service


def test_build_console_log_html_escapes_input():
    html_text = console_log_service.build_console_log_html(
        "INP <done>",
        ["path: D:/demo/a&b.inp", "line2"],
    )

    assert html_text.startswith("<div><strong>INP &lt;done&gt;</strong><br/>")
    assert "a&amp;b.inp" in html_text
    assert "line2" in html_text


def test_safe_write_console_event_swallows_logging_errors(monkeypatch):
    monkeypatch.setattr(
        console_log_service,
        "write_console_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    console_log_service.safe_write_console_event(10, "test", ["detail"])
