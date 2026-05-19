from pal.cli import render_splash_commands, _all_command_classes


def test_splash_contains_every_command_name():
    text = render_splash_commands()
    for cls in _all_command_classes():
        assert f"/{cls.name}" in text, f"/{cls.name} missing from splash"


def test_splash_includes_pal_specific_commands():
    text = render_splash_commands()
    # PAL-specific commands that must appear
    for name in ("lint", "import", "learn"):
        assert f"/{name}" in text, f"/{name} missing from splash"
