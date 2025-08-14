// ===== إعداد معرف العميل الفريد =====
const clientId = (crypto.randomUUID && crypto.randomUUID()) || (Date.now() + "-" + Math.random());

// ===== إنشاء اتصال Socket.IO =====
const socket = io();

socket.on("connect", () => {
  socket.emit("register", { clientId });
  if (window.uiSetConnection) window.uiSetConnection(true);
});

socket.on("disconnect", () => {
  if (window.uiSetConnection) window.uiSetConnection(false);
});

socket.on("registered", (data) => {
  console.log("Registered as", data.clientId);
});

// ===== استلام الترتيب من الخادم وعرضه =====
socket.on("ranking", (payload) => {
  const list = payload.ranked || [];
  if (window.uiRenderRanking) {
    window.uiRenderRanking(list);
  } else {
    // عرض بديل بسيط إذا ما فيه دالة واجهة
    const listEl = document.getElementById("results");
    if (listEl) {
      listEl.innerHTML = "";
      list.forEach((s, i) => {
        const li = document.createElement("li");
        li.textContent = `#${i + 1} | ${s.id} - ${s.avg_db} dB ${s.speech ? "🗣️" : ""}`;
        listEl.appendChild(li);
      });
    }
  }
});

// ===== التقاط الصوت من الميكروفون وإرساله =====
async function startAudio() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  if (window.uiSetMic) {
    const label = stream.getAudioTracks()[0] ? stream.getAudioTracks()[0].label : 'مُفعّل';
    window.uiSetMic(true, label);
  }

  const context = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
  const source = context.createMediaStreamSource(stream);

  // ScriptProcessorNode (حل بسيط – رغم أنه مُهمّش)
  const processor = context.createScriptProcessor(4096, 1, 1);
  source.connect(processor);
  processor.connect(context.destination);

  const targetRate = 16000;
  const decim = Math.floor(context.sampleRate / targetRate);
  let downsampleBuffer = [];

  processor.onaudioprocess = (e) => {
    const input = e.inputBuffer.getChannelData(0); // Float32 [-1,1]
    if (decim <= 0) return;

    for (let i = 0; i < input.length; i += decim) {
      downsampleBuffer.push(input[i]);
    }

    // أرسل كل ~200ms
    const samplesPerPacket = targetRate * 0.2; // 3200 عينة
    if (downsampleBuffer.length >= samplesPerPacket) {
      const chunk = downsampleBuffer.splice(0, samplesPerPacket);
      const pcm16 = floatTo16BitPCM(chunk);
      socket.emit("audio", { clientId, pcm: pcm16 });
    }
  };
}

// تحويل Float32 إلى PCM16
function floatTo16BitPCM(float32Array) {
  const buf = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buf);
  let offset = 0;
  for (let i = 0; i < float32Array.length; i++, offset += 2) {
    let s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true); // Little-endian
  }
  return new Uint8Array(buf);
}

// ===== أزرار التحكم =====
document.getElementById("startBtn").addEventListener("click", () => {
  startAudio().catch(err => {
    alert(err.message);
    if (window.uiSetMic) window.uiSetMic(false);
  });
});

document.getElementById("saveTopBtn").addEventListener("click", async () => {
  const res = await fetch("/download_top.wav");
  const j = await res.json();
  if (j.file) {
    alert(`تم حفظ: ${j.file}`);
  } else {
    alert(j.error || "لا يوجد ملف");
  }
});

