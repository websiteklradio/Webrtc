import asyncio
import json
import sounddevice as sd
import numpy as np
import av
import fractions
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack

# ================= CONFIG =================

SIGNALING_URL = "wss://webrtc-signaling-backend.onrender.com/ws"
SAMPLE_RATE = 48000
DEVICE_INDEX = 4  # change if needed

ICE_SERVERS = {
    "iceServers": [
        {"urls": ["stun:stun.l.google.com:19302"]},
        {
            "urls": ["turn:openrelay.metered.ca:80"],
            "username": "openrelayproject",
            "credential": "openrelayproject",
        },
        {
            "urls": ["turn:openrelay.metered.ca:443?transport=tcp"],
            "username": "openrelayproject",
            "credential": "openrelayproject",
        },
        {
            "urls": ["turns:openrelay.metered.ca:443"],
            "username": "openrelayproject",
            "credential": "openrelayproject",
        },
    ]
}

# =========================================

audio_queue = asyncio.Queue()

def audio_callback(indata, frames, time, status):
    pcm = (indata[:, 0] * 32767).astype(np.int16)
    asyncio.get_event_loop().call_soon_threadsafe(
        audio_queue.put_nowait, pcm
    )

class AudioTrack(MediaStreamTrack):
    kind = "audio"

    async def recv(self):
        pcm = await audio_queue.get()
        frame = av.AudioFrame.from_ndarray(pcm, layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        return frame

async def run_sender():
    peers = {}
    audio_track = AudioTrack()

    # 🎙️ Start microphone capture
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        device=DEVICE_INDEX,
        callback=audio_callback,
        blocksize=960  # 🔥 low latency (~20ms)
    )
    stream.start()

    async with websockets.connect(SIGNALING_URL) as ws:
        await ws.send(json.dumps({
            "type": "join",
            "roomId": "radio-live-room",
            "role": "sender"
        }))
        print("🎙️ Sender registered")

        async for raw in ws:
            msg = json.loads(raw)

            # 👂 Listener joined
            if msg["type"] == "listener_joined":
                listener_id = msg["listenerId"]

                pc = RTCPeerConnection(ICE_SERVERS)
                pc.addTrack(audio_track)
                peers[listener_id] = pc

                # 🔁 Send ICE candidates to listener
                @pc.on("icecandidate")
                async def on_icecandidate(candidate):
                    if candidate:
                        await ws.send(json.dumps({
                            "type": "candidate",
                            "candidate": {
                                "candidate": candidate.candidate,
                                "sdpMid": candidate.sdpMid,
                                "sdpMLineIndex": candidate.sdpMLineIndex,
                            },
                            "listenerId": listener_id,
                        }))

                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)

                await ws.send(json.dumps({
                    "type": "offer",
                    "offer": {
                        "type": offer.type,
                        "sdp": offer.sdp,
                    },
                    "listenerId": listener_id,
                }))

            # 📩 Answer from listener
            elif msg["type"] == "answer":
                pc = peers.get(msg["listenerId"])
                if pc:
                    await pc.setRemoteDescription(
                        RTCSessionDescription(
                            msg["answer"]["sdp"],
                            msg["answer"]["type"]
                        )
                    )

            # ❄️ ICE candidate from listener
            elif msg["type"] == "candidate":
                pc = peers.get(msg.get("listenerId"))
                if pc and msg.get("candidate"):
                    await pc.addIceCandidate(msg["candidate"])

if __name__ == "__main__":
    asyncio.run(run_sender())
