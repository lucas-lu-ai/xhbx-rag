def test_web_app_imports() -> None:
    from xhbx_rag.web.app import create_app

    app = create_app()

    assert app.title == "xhbx-rag Web"
