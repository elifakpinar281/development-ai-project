from app.ui import build_ui, launch_options


def main() -> None:
    """Entry point script for launching the application."""
    demo = build_ui()
    demo.launch(**launch_options())


if __name__ == "__main__":
    main()