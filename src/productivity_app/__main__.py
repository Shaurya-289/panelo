"""Backend bootstrap entry point; no user interface is started here."""

from productivity_app.bootstrap import create_application


def main() -> None:
    application = create_application()
    application.close()


if __name__ == "__main__":
    main()
