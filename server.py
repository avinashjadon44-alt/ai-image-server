from flask import Flask, jsonify, request, Response
import os
import re
import requests
from PIL import Image
from io import BytesIO

app = Flask(__name__)

IMAGE_FOLDER = "images"
os.makedirs(IMAGE_FOLDER, exist_ok=True)

TOPIC_FILE = "current_topic.txt"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
VOICERSS_KEY = os.environ.get("VOICERSS_KEY")

HEADERS = {
    "User-Agent": "ESP32-Backend"
}

RAW_W = 320
RAW_H = 160


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


def fit_image_centered(img, target_w, target_h, bg_color=(255, 255, 255)):
    img = img.convert("RGB")
    src_w, src_h = img.size

    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))

    img = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGB", (target_w, target_h), bg_color)
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    canvas.paste(img, (x, y))
    return canvas


def convert_image_to_raw(img, raw_path):
    img = fit_image_centered(img, RAW_W, RAW_H)

    with open(raw_path, "wb") as f:
        for y in range(RAW_H):
            for x in range(RAW_W):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(bytes([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF]))


def make_gray_raw(raw_path):
    img = Image.new("RGB", (RAW_W, RAW_H), (96, 96, 96))
    convert_image_to_raw(img, raw_path)
    return raw_path


def wikipedia_thumbnail_url(topic):
    api_url = "https://en.wikipedia.org/w/api.php"

    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": topic,
        "gsrlimit": 1,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": 800,
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


def get_short_text(topic):
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
    res.raise_for_status()

    data = res.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


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
        "&f=8khz_8bit_mono_pcm&codec=PCM"
    )

    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()

    return Response(
        r.iter_content(512),
        content_type="application/octet-stream"
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

        return jsonify({
            "topic": topic,
            "thumbnail_url": url,
            "cached_exists": os.path.exists(raw_path),
            "raw_path": raw_path,
            "raw_size": f"{RAW_W}x{RAW_H}"
        })
    except Exception as e:
        return jsonify({
            "topic": topic,
            "error": str(e)
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
