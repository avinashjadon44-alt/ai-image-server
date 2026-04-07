from flask import Flask, send_file, jsonify, request, Response
import os
import re
import requests
from PIL import Image
from io import BytesIO

app = Flask(__name__)

IMAGE_FOLDER = "images"
os.makedirs(IMAGE_FOLDER, exist_ok=True)

GEMINI_KEY = os.environ.get("GEMINI_KEY")
VOICERSS_KEY = os.environ.get("VOICERSS_KEY")

HEADERS = {
    "User-Agent": "ESP32-Backend"
}

# -------------------------
# HELPERS
# -------------------------
def normalize_topic(topic):
    topic = topic.strip().lower()
    topic = topic.replace(" ", "_")
    topic = re.sub(r"[^a-z0-9_()-]", "", topic)
    return topic


def make_gray_raw(raw_path):
    img = Image.new("RGB", (320, 240), (96, 96, 96))
    convert_image_to_raw(img, raw_path)
    return raw_path


# -------------------------
# IMAGE
# -------------------------
def wikipedia_thumbnail_url(topic):
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
    img = img.convert("RGB")
    img = img.resize((320, 240))

    with open(raw_path, "wb") as f:
        for y in range(240):
            for x in range(320):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(bytes([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF]))


def fetch_and_convert(topic, force_refresh=False):
    safe = normalize_topic(topic)
    raw_path = os.path.join(IMAGE_FOLDER, safe + ".raw")

    if os.path.exists(raw_path) and not force_refresh:
        print("Using cached RAW image:", raw_path)
        return raw_path

    try:
        url = wikipedia_thumbnail_url(topic)
        if not url:
            raise Exception("No thumbnail URL found from Wikipedia")

        print("Thumbnail URL:", url)

        img_res = requests.get(url, timeout=20, headers=HEADERS)
        img_res.raise_for_status()

        img = Image.open(BytesIO(img_res.content))
        convert_image_to_raw(img, raw_path)

        print("RAW image created:", raw_path)
        return raw_path

    except Exception as e:
        print("IMAGE ERROR:", str(e))
        return make_gray_raw(raw_path)


# -------------------------
# ROUTES
# -------------------------
@app.route("/")
def home():
    return "OK"


@app.route("/image/<topic>")
def image(topic):
    refresh = request.args.get("refresh", "0") == "1"
    path = fetch_and_convert(topic, force_refresh=refresh)
    return send_file(path, mimetype="application/octet-stream")


@app.route("/tts")
def tts():
    text = request.args.get("text", "").strip()

    if not text:
        return Response("Missing text", status=400)

    if not VOICERSS_KEY:
        return Response("VOICERSS_KEY not set", status=500)

    url = (
        "https://api.voicerss.org/?key=" + VOICERSS_KEY +
        "&hl=en-us&src=" + requests.utils.quote(text) +
        "&f=8khz_8bit_mono_pcm&codec=PCM"
    )

    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()

    return Response(
        r.iter_content(512),
        content_type="application/octet-stream"
    )


# -------------------------
# FULL PIPELINE
# -------------------------
@app.route("/full/<topic>")
def full(topic):
    try:
        # IMAGE
        fetch_and_convert(topic)

        # GEMINI
        if not GEMINI_KEY:
            return jsonify({"error": "GEMINI_KEY not set"}), 500

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent?key=" + GEMINI_KEY
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

        res = requests.post(url, json=payload, timeout=30)
        res.raise_for_status()

        data = res.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]

        return jsonify({
            "text": text
        })

    except Exception as e:
        print("FULL ERROR:", str(e))
        return jsonify({"error": str(e)}), 500


# -------------------------
# OPTIONAL DEBUG ROUTE
# -------------------------
@app.route("/debug_image/<topic>")
def debug_image(topic):
    try:
        url = wikipedia_thumbnail_url(topic)
        return jsonify({
            "topic": topic,
            "thumbnail_url": url
        })
    except Exception as e:
        return jsonify({
            "topic": topic,
            "error": str(e)
        }), 500


# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
