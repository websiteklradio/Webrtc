# backend/audio_track.py
from aiortc import MediaStreamTrack
from av import AudioFrame
import numpy as np
import asyncio

class AudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.queue = asyncio.Queue()

    async def recv(self):
        frame = await self.queue.get()
        return frame

    def push(self, samples):
        frame = AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = 48000
        self.queue.put_nowait(frame)
