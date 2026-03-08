from src.app import create_app
from src.config import PORT

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
