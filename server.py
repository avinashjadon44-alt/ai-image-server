from flask import Flask, send_file, jsonify, request, Response
import os
import re
import json
import requests
from PIL import Image
from io import BytesIO

app = Flask(__name__)

IMAGE_FOLDER = "images"
os.makedirs(IMAGE_FOLDER, exist_ok=True)

TOPIC_FILE = "current_topic.txt"
TEXT_CACHE_FILE = "text_cache.json"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
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


def save_topic(topic):
    topic = topic.strip()
    with open(TOPIC_FILE, "w", encoding="utf-8") as f:
        f.write(topic)


def load_topic():
    if not os.path.exists(TOPIC_FILE):
        return "taj mahal"

    with open(TOPIC_FILE, "r", encoding="utf-8") as f:
        topic = f.read().strip()

    if not topic:
        return "taj mahal"

    return topic


def load_text_cache():
    if not os.path.exists(TEXT_CACHE_FILE):
        return {}

    try:
        with open(TEXT_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_text_cache(cache):
    with open(TEXT_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


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
# GEMINI TEXT ONLY
# -------------------------
def get_short_text(topic):
    cache = load_text_cache()
    topic_key = normalize_topic(topic)

    if topic_key in cache:
        print("Using cached Gemini text:", topic_key)
        return cache[topic_key]

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent?key=" + GEMINI_API_KEY
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "In 5 words only: " + topic}
                ]
            }
        ]
    }

    res = requests.post(url, json=payload, timeout=30)

    if res.status_code == 429:
        raise RuntimeError("Gemini rate limit hit. Please wait and try again.")

    res.raise_for_status()

    data = res.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

    if not text:
        raise RuntimeError("Gemini returned empty text")

    cache[topic_key] = text
    save_text_cache(cache)

    return text


# -------------------------
# ROUTES
# -------------------------
@app.route("/")
def home():
    return "OK"


@app.route("/topic_page")
def topic_page():
    current = load_topic()
    return f"""
    <html>
    <head>
        <title>Set Topic</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
    </head>
    <body style="font-family: Arial, sans-serif; padding: 24px; background: #f5f5f5;">
        <div style="max-width: 500px; margin: auto; background: white; padding: 24px; border-radius: 12px;">
            <h2>Set ESP Topic</h2>
            <form action="/set_topic" method="post">
                <input
                    type="text"
                    name="topic"
                    value="{current}"
                    placeholder="Enter topic"
                    style="width: 100%; height: 44px; font-size: 18px; padding: 8px; margin-bottom: 12px;"
                />
                <button
                    type="submit"
                    style="width: 100%; height: 44px; font-size: 18px; cursor: pointer;"
                >
                    Save Topic
                </button>
            </form>
            <p style="margin-top: 16px;">
                Current topic: <b>{current}</b>
            </p>
        </div>
    </body>
    </html>
    """


@app.route("/set_topic", methods=["GET", "POST"])
def set_topic():
    if request.method == "POST":
        topic = request.form.get("topic", "").strip()
    else:
        topic = request.args.get("topic", "").strip()

    if not topic:
        return jsonify({"ok": False, "error": "Missing topic"}), 400

    save_topic(topic)
    return jsonify({"ok": True, "topic": topic})


@app.route("/get_topic")
def get_topic():
    topic = load_topic()
    return jsonify({"topic": topic})


@app.route("/image/<topic>")
def image(topic):
    refresh = request.args.get("refresh", "0") == "1"
    path = fetch_and_convert(topic, force_refresh=refresh)

    file_size = os.path.getsize(path)

    def generate():
        with open(path, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                yield chunk

    return Response(
        generate(),
        mimetype="application/octet-stream",
        headers={
            "Content-Length": str(file_size),
            "Cache-Control": "no-cache"
        }
    )


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
        "&f=16khz_16bit_mono&c=WAV"
    )

    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()

    return Response(
        r.iter_content(1024),
        content_type="audio/wav"
    )


@app.route("/full/<topic>")
def full(topic):
    try:
        text = get_short_text(topic)
        return jsonify({"text": text})
    except Exception as e:
        print("FULL ERROR:", str(e))
        return jsonify({"error": str(e)}), 500


@app.route("/debug_image/<topic>")
def debug_image(topic):
    try:
        url = wikipedia_thumbnail_url(topic)
        safe = normalize_topic(topic)
        raw_path = os.path.join(IMAGE_FOLDER, safe + ".raw")

        cache = load_text_cache()
        return jsonify({
            "topic": topic,
            "thumbnail_url": url,
            "cached_exists": os.path.exists(raw_path),
            "raw_path": raw_path,
            "text_cache_exists": normalize_topic(topic) in cache
        })
    except Exception as e:
        return jsonify({
            "topic": topic,
            "error": str(e)
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
