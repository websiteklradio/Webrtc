from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uuid
import json
import asyncio
from typing import Dict

app = FastAPI()

# roomId -> { sender: client_id, listeners: { client_id: WebSocket } }
rooms: Dict[str, Dict] = {}
connections: Dict[str, WebSocket] = {}


async def keep_alive(ws: WebSocket):
    while True:
        await asyncio.sleep(25)
        await ws.send_text(json.dumps({"type": "ping"}))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    asyncio.create_task(keep_alive(ws))

    client_id = str(uuid.uuid4())
    connections[client_id] = ws

    role = None
    room_id = None

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            # ---------------- JOIN ----------------
            if msg_type == "join":
                role = data["role"]
                room_id = data["roomId"]

                if room_id not in rooms:
                    rooms[room_id] = {
                        "sender": None,
                        "listeners": {}
                    }

                if role == "sender":
                    rooms[room_id]["sender"] = client_id
                    print("Sender joined:", room_id)

                    # 🔥 KEY FIX: notify sender about existing listeners
                    for listener_id in rooms[room_id]["listeners"]:
                        await ws.send_text(json.dumps({
                            "type": "listener_joined",
                            "listenerId": listener_id
                        }))

                elif role == "listener":
                    rooms[room_id]["listeners"][client_id] = ws
                    print("Listener joined:", client_id)

                    sender_id = rooms[room_id]["sender"]
                    if sender_id:
                        await connections[sender_id].send_text(json.dumps({
                            "type": "listener_joined",
                            "listenerId": client_id
                        }))

            # ---------------- OFFER ----------------
            elif msg_type == "offer":
                listener_id = data["listenerId"]
                if listener_id in connections:
                    await connections[listener_id].send_text(json.dumps({
                        "type": "offer",
                        "offer": data["offer"],
                        "listenerId": listener_id
                    }))

            # ---------------- ANSWER ----------------
            elif msg_type == "answer":
                sender_id = rooms[room_id]["sender"]
                if sender_id:
                    await connections[sender_id].send_text(json.dumps({
                        "type": "answer",
                        "answer": data["answer"],
                        "listenerId": data["listenerId"]
                    }))

            # ---------------- ICE ----------------
            elif msg_type == "candidate":
                listener_id = data.get("listenerId")
                target_id = listener_id or rooms[room_id]["sender"]

                if target_id in connections:
                    await connections[target_id].send_text(json.dumps({
                        "type": "candidate",
                        "candidate": data["candidate"],
                        "listenerId": listener_id
                    }))

            # ---------------- END ----------------
            elif msg_type == "broadcast_end":
                room = rooms.pop(room_id, None)
                if room:
                    for ws in room["listeners"].values():
                        await ws.send_text(json.dumps({"type": "broadcast_end"}))

    except WebSocketDisconnect:
        pass

    finally:
        connections.pop(client_id, None)
        if room_id and room_id in rooms:
            if role == "listener":
                rooms[room_id]["listeners"].pop(client_id, None)
            if role == "sender":
                for ws in rooms[room_id]["listeners"].values():
                    await ws.send_text(json.dumps({"type": "broadcast_end"}))
                rooms.pop(room_id, None)
