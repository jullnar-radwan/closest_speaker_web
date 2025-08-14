import os
import wave
import uuid
import numpy as np
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import webrtcvad
from collections import deque

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

SAMPLE_RATE = 16000          # نرسل من المتصفح 16kHz أحادي
VAD_FRAME_MS = 20            # 10/20/30 ms - نستخدم 20ms
VAD = webrtcvad.Vad(2)       # شدة 0-3 (2 وسط)
BYTES_PER_SAMPLE = 2         # int16

# حالة كل عميل: نافذة dB، حالة الكلام، وتخزين آخر بيانات للصوت
clients = {}  # client_id -> {"db_window": deque, "speech": bool, "buffer": bytearray}

def pcm16_to_db(pcm_bytes: bytes) -> float:
    if not pcm_bytes:
        return -120.0
    arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    if arr.size == 0:
        return -120.0
    rms = np.sqrt(np.mean((arr / 32768.0) ** 2))
    return float(20 * np.log10(rms + 1e-9))

def chunk_iter(pcm_bytes: bytes, frame_ms=VAD_FRAME_MS):
    bytes_per_frame = int(SAMPLE_RATE * (frame_ms / 1000.0) * BYTES_PER_SAMPLE)
    for i in range(0, len(pcm_bytes) - bytes_per_frame + 1, bytes_per_frame):
        yield pcm_bytes[i:i + bytes_per_frame]

def speech_present(pcm_bytes: bytes) -> bool:
    for frame in chunk_iter(pcm_bytes, VAD_FRAME_MS):
        if VAD.is_speech(frame, SAMPLE_RATE):
            return True
    return False

@app.route("/")
def index():
    return render_template("index.html")

@socketio.on("connect")
def on_connect():
    emit("connected", {"ok": True})

@socketio.on("register")
def on_register(data):
    client_id = data.get("clientId")
    if client_id:
        clients[client_id] = {
            "db_window": deque(maxlen=10),
            "speech": False,
            "buffer": bytearray()
        }
        emit("registered", {"clientId": client_id})

@socketio.on("audio")
def on_audio(data):
    """
    يستقبل ArrayBuffer (PCM int16 mono 16kHz) من المتصفح.
    data: {clientId: str, pcm: bytes}
    """
    client_id = data.get("clientId")
    pcm = data.get("pcm")
    if client_id is None or pcm is None:
        return

    # حساب dB + VAD
    db = pcm16_to_db(pcm)
    sp = speech_present(pcm)

    state = clients.get(client_id)
    if state is None:
        return
    state["db_window"].append(db)
    state["speech"] = sp
    # نخزن فقط إذا فيه كلام (تقليل الضوضاء والتحميل)
    if sp:
        state["buffer"].extend(pcm)

    # ترتيب المتكلمين النشطين حسب متوسط dB
    ranked = []
    for cid, st in clients.items():
        if len(st["db_window"]) == 0:
            continue
        avg_db = float(np.mean(st["db_window"]))
        ranked.append({"id": cid, "avg_db": round(avg_db, 2), "speech": st["speech"]})

    ranked.sort(key=lambda x: x["avg_db"], reverse=True)

    # إرسال الترتيب للجميع
    socketio.emit("ranking", {"ranked": ranked})

@socketio.on("disconnect")
def on_disconnect():
    # لا نعرف clientId هنا بسهولة، يُترك كما هو (بسيط)
    pass

@app.route("/download_top.wav", methods=["GET"])
def download_top():
    """
    يحفظ صوت أقرب متحدث نشط مؤخرًا إلى WAV ليستخدم لاحقًا مع ASR.
    """
    if not clients:
        return jsonify({"error": "no clients"}), 400

    # اختر الأعلى dB من آخر ترتيب بسيط
    best_id = None
    best_db = -999
    for cid, st in clients.items():
        if len(st["db_window"]) == 0:
            continue
        avg_db = float(np.mean(st["db_window"]))
        if avg_db > best_db:
            best_db = avg_db
            best_id = cid

    if best_id is None:
        return jsonify({"error": "no audio"}), 400

    pcm = clients[best_id]["buffer"]
    if len(pcm) < SAMPLE_RATE * BYTES_PER_SAMPLE:  # أقل من ثانية
        return jsonify({"error": "not enough audio"}), 400

    fname = f"top_{best_id[:8]}.wav"
    with wave.open(fname, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(BYTES_PER_SAMPLE)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(bytes(pcm))
    # تفريغ البافر بعد الحفظ (اختياري)
    clients[best_id]["buffer"].clear()
    return jsonify({"file": fname, "clientId": best_id, "avg_db": round(best_db, 2)}), 200

if __name__ == "__main__":
    # تشغيل بسيط للتجربة
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)

