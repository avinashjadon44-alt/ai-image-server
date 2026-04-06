from flask import Flask, send_file, jsonify
import os
import re
import requests
from PIL import Image

app = Flask(__name__)

IMAGE_FOLDER = "images"
os.makedirs(IMAGE_FOLDER, exist_ok=True)

GEMINI_KEY = "AIzaSyAaGGvPq5i_otoTlVbjUan6Sd6QWIth3x0"

HEADERS = {
    "User-Agent": "ESP32-Image-Backend/1.0"
}


def normalize_topic(topic):
    """
    Make topic safe for filenames and URLs.
    Example: 'red car' -> 'red_car'
    """
    topic = topic.strip().lower()
    topic = topic.replace(" ", "_")
    topic = re.sub(r"[^a-z0-9_()-]", "", topic)
    return topic


def wikipedia_thumbnail_url(topic):
    """
    Search Wikipedia for the topic and get a thumbnail URL.
    """
    api_url = "https://en.wikipedia.org/w/api.php"

    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": topic,
        "gsrlimit": 1,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": 640,
        "format": "json"
    }

    res = requests.get(api_url, params=params, headers=HEADERS, timeout=20)
    res.raise_for_status()
    data = res.json()

    pages = data.get("query", {}).get("pages", {})
    for _, page in pages.items():
        thumb = page.get("thumbnail", {})
        source = thumb.get("source")
        if source:
            return source

    return None


def convert_image_to_raw(img, raw_path):
    """
    Convert PIL image to 320x240 RGB565 raw file.
    """
    img = img.convert("RGB")
    img = img.resize((320, 240))

    with open(raw_path, "wb") as f:
        for y in range(240):
            for x in range(320):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(bytes([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF]))


def fetch_and_convert(topic):
    safe_topic = normalize_topic(topic)
    raw_path = os.path.join(IMAGE_FOLDER, safe_topic + ".raw")
    jpg_path = os.path.join(IMAGE_FOLDER, safe_topic + ".jpg")

    # Use cached raw if it already exists
    if os.path.exists(raw_path):
        print("Using cached raw:", raw_path)
        return raw_path

    print("Fetching image for:", topic)

    try:
        thumb_url = wikipedia_thumbnail_url(topic)

        if not thumb_url:
            raise Exception("No thumbnail found from Wikipedia")

        print("Thumbnail URL:", thumb_url)

        img_res = requests.get(thumb_url, headers=HEADERS, timeout=20)
        img_res.raise_for_status()

        content_type = img_res.headers.get("Content-Type", "")
        if "image" not in content_type.lower():
            raise Exception("Thumbnail response is not an image")

        with open(jpg_path, "wb") as f:
            f.write(img_res.content)

        img = Image.open(jpg_path)
        convert_image_to_raw(img, raw_path)

        print("Generated raw:", raw_path)
        return raw_path

    except Exception as e:
        print("Image fetch failed:", e)
        print("Generating fallback raw...")

        # Fallback: neutral gray image
        img = Image.new("RGB", (320, 240), (96, 96, 96))
        convert_image_to_raw(img, raw_path)

        print("Fallback raw created:", raw_path)
        return raw_path


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "routes": [
            "/image/<topic>",
            "/gemini/<topic>"
        ]
    })


@app.route("/image/<topic>")
def get_image(topic):
    try:
        path = fetch_and_convert(topic)
        return send_file(path, mimetype="application/octet-stream")
    except Exception as e:
        return str(e), 500


@app.route("/gemini/<topic>")
def gemini_text(topic):
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.1-flash-lite-preview:generateContent?key=" + GEMINI_KEY
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "In 5 words: " + topic}
                ]
            }
        ]
    }

    try:
        res = requests.post(url, json=payload, headers=HEADERS, timeout=20)
        res.raise_for_status()
        data = res.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"text": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)