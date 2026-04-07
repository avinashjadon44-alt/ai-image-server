from flask import Flask, send_file, jsonify, request, Response
import os
import re
import requests
from PIL import Image

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


def fetch_and_convert(topic):
    safe = normalize_topic(topic)
    raw_path = os.path.join(IMAGE_FOLDER, safe + ".raw")

    if os.path.exists(raw_path):
        return raw_path

    try:
        url = wikipedia_thumbnail_url(topic)
        img_res = requests.get(url, timeout=20)

        img = Image.open(requests.compat.BytesIO(img_res.content))
        convert_image_to_raw(img, raw_path)

        return raw_path

    except:
        img = Image.new("RGB", (320, 240), (96, 96, 96))
        convert_image_to_raw(img, raw_path)
        return raw_path


# -------------------------
# ROUTES
# -------------------------
@app.route("/")
def home():
    return "OK"


@app.route("/image/<topic>")
def image(topic):
    path = fetch_and_convert(topic)
    return send_file(path, mimetype="application/octet-stream")


@app.route("/tts")
def tts():
    text = request.args.get("text", "")
    text = text.replace("%20", " ").strip()

    url = (
        "https://api.voicerss.org/?key=" + VOICERSS_KEY +
        "&hl=en-us&src=" + requests.utils.quote(text) +
        "&f=8khz_8bit_mono_pcm&codec=PCM"
    )

    r = requests.get(url, stream=True)

    return Response(r.iter_content(512),
                    content_type="application/octet-stream")


# -------------------------
# 🔥 FULL PIPELINE
# -------------------------
@app.route("/full/<topic>")
def full(topic):
    try:
        # IMAGE
        fetch_and_convert(topic)

        # GEMINI
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + GEMINI_KEY

        payload = {
            "contents": [
                {"parts": [{"text": "In 5 words: " + topic}]}
            ]
        }

        res = requests.post(url, json=payload, timeout=20)
        data = res.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]

        return jsonify({
            "text": text
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
