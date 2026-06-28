"""Entry point for `python3 -m white_wizard` and the `wizard` CLI command."""
from white_wizard import ai_client, app
from white_wizard import db as wizard_db


def main():
    wizard_db.init_db()
    ai_client.parse_args()
    ai_client.enable_debug_logging()  # always-on AI prompt/response trace
    try:
        app.main()
    except (KeyboardInterrupt, EOFError):
        app._shutdown_stream_workers()
        from white_wizard.app import color, DIM, WHITE
        print(color("\n\n  The staff dims. Farewell.\n", DIM, WHITE))
    except RuntimeError as exc:
        app._shutdown_stream_workers()
        from white_wizard.app import color, DIM, WHITE
        print(color(f"\n\n  The spell fizzles — {exc}\n", DIM, WHITE))


if __name__ == "__main__":
    main()
