import os

from .server import serve

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    serve(port=port, base_url=os.environ.get("BASE_URL")).serve_forever()
