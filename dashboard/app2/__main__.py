from . import create_app
import os

if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, port=port, use_reloader=False)
