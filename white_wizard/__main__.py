"""Entry point for `python3 -m white_wizard` and the `wizard` CLI command."""
from white_wizard import ai_client, app
from white_wizard import db as wizard_db


def main():
    wizard_db.init_db()
    flags = ai_client.parse_args()
    try:
        if flags["stream"]:
            app.run_stream_mode()
        else:
            app.main()
    except (KeyboardInterrupt, EOFError):
        from white_wizard.app import color, DIM, WHITE
        print(color("\n\n  The staff dims. Farewell.\n", DIM, WHITE))


if __name__ == "__main__":
    main()
