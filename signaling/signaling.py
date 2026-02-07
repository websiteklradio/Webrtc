from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uuid
import json
import asyncio
from typing import Dict, Optional, Any

app = FastAPI()

# roomId -> {"sender": Optional[client_id], "listeners": Dict[client_id, WebSocket]}
rooms: Dict[str, Dict[str, Any]] = {}
# client_id -> WebSocket
connections: Dict[str, WebSocket] = {}
# client_id -> {"role": "sender"|"listener", "room_id": str}
client_meta: Dict[str, Dict[str, str]] = {}


async def safe_send(ws: WebSocket, payload: dict) -> bool:
    """Send JSON safely. Returns False if send failed."""
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception:
        return False


async def keep_alive(ws: WebSocket, stop_event: asyncio.Event):
    """Periodic ping to keep mobile connections alive."""
    try:
        while not stop_event.is_set():
            await asyncio.sleep(25)
            # If connection is already closing/closed, this will throw and exit.
            await ws.send_text(json.dumps({"type": "ping"}))
    except Exception:
        # socket likely closed; exit quietly
        return


def ensure_room(room_id: str):
    if room_id not in rooms:
        rooms[room_id] = {"sender": None, "listeners": {}}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    client_id = str(uuid.uuid4())
    connections[client_id] = ws
    client_meta[client_id] = {"role": "", "room_id": ""}

    stop_ping = asyncio.Event()
    ping_task = asyncio.create_task(keep_alive(ws, stop_ping))

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            # Ignore keep-alive pings from client
            if msg_type == "ping":
                continue

            # Current client context
            role = client_meta[client_id]["role"] or None
            room_id = client_meta[client_id]["room_id"] or None

            # ---------------- JOIN ----------------
            if msg_type == "join":
                role = data.get("role")
                room_id = data.get("roomId")

                if role not in ("sender", "listener") or not room_id:
                    await safe_send(ws, {"type": "error", "message": "Invalid join payload"})
                    continue

                client_meta[client_id] = {"role": role, "room_id": room_id}
                ensure_room(room_id)

                if role == "sender":
                    rooms[room_id]["sender"] = client_id
                    print(f"Sender joined room={room_id} sender_id={client_id}")

                    # Notify sender about existing listeners (so sender can create offers per listener)
                    for listener_id in list(rooms[room_id]["listeners"].keys()):
                        await safe_send(ws, {"type": "listener_joined", "listenerId": listener_id})

                else:  # listener
                    rooms[room_id]["listeners"][client_id] = ws
                    print(f"Listener joined room={room_id} listener_id={client_id}")

                    # Notify sender that a new listener joined (so sender creates a new offer)
                    sender_id = rooms[room_id]["sender"]
                    if sender_id and sender_id in connections:
                        await safe_send(
                            connections[sender_id],
                            {"type": "listener_joined", "listenerId": client_id},
                        )
                    else:
                        # Optional: tell listener there is no sender yet
                        await safe_send(ws, {"type": "no_sender", "roomId": room_id})

            # If not joined yet, ignore everything else
            elif not role or not room_id:
                await safe_send(ws, {"type": "error", "message": "Must join first"})
                continue

            # ---------------- OFFER (sender -> listener) ----------------
            elif msg_type == "offer":
                # Sender must target a specific listener
                listener_id = data.get("listenerId")
                offer = data.get("offer")
                if not listener_id or not offer:
                    await safe_send(ws, {"type": "error", "message": "offer requires listenerId and offer"})
                    continue

                # Only sender should send offers
                if role != "sender":
                    await safe_send(ws, {"type": "error", "message": "Only sender can send offers"})
                    continue

                target_ws = rooms.get(room_id, {}).get("listeners", {}).get(listener_id)
                if target_ws:
                    await safe_send(target_ws, {"type": "offer", "offer": offer, "listenerId": listener_id})
                else:
                    await safe_send(ws, {"type": "error", "message": f"Listener not found: {listener_id}"})

            # ---------------- ANSWER (listener -> sender) ----------------
            elif msg_type == "answer":
                answer = data.get("answer")
                listener_id = data.get("listenerId") or client_id  # fallback

                if not answer:
                    await safe_send(ws, {"type": "error", "message": "answer requires answer"})
                    continue

                # Only listener should send answers
                if role != "listener":
                    await safe_send(ws, {"type": "error", "message": "Only listener can send answers"})
                    continue

                sender_id = rooms.get(room_id, {}).get("sender")
                if sender_id and sender_id in connections:
                    await safe_send(
                        connections[sender_id],
                        {"type": "answer", "answer": answer, "listenerId": listener_id},
                    )
                else:
                    await safe_send(ws, {"type": "error", "message": "No sender in room"})

            # ---------------- ICE CANDIDATE ----------------
            elif msg_type == "candidate":
                candidate = data.get("candidate")
                if not candidate:
                    await safe_send(ws, {"type": "error", "message": "candidate requires candidate"})
                    continue

                if role == "listener":
                    # Listener ICE must go to sender
                    sender_id = rooms.get(room_id, {}).get("sender")
                    if sender_id and sender_id in connections:
                        await safe_send(
                            connections[sender_id],
                            {
                                "type": "candidate",
                                "candidate": candidate,
                                "listenerId": client_id,  # ✅ CRITICAL so sender maps to correct PC
                            },
                        )
                    else:
                        await safe_send(ws, {"type": "error", "message": "No sender in room"})

                else:  # role == "sender"
                    # Sender ICE must go to a specific listener
                    listener_id = data.get("listenerId")
                    if not listener_id:
                        await safe_send(ws, {"type": "error", "message": "sender candidate requires listenerId"})
                        continue

                    target_ws = rooms.get(room_id, {}).get("listeners", {}).get(listener_id)
                    if target_ws:
                        await safe_send(
                            target_ws,
                            {"type": "candidate", "candidate": candidate, "listenerId": listener_id},
                        )
                    else:
                        await safe_send(ws, {"type": "error", "message": f"Listener not found: {listener_id}"})

            # ---------------- END BROADCAST (sender) ----------------
            elif msg_type == "broadcast_end":
                # Only sender should end
                if role != "sender":
                    await safe_send(ws, {"type": "error", "message": "Only sender can end broadcast"})
                    continue

                room = rooms.pop(room_id, None)
                if room:
                    for lws in list(room["listeners"].values()):
                        await safe_send(lws, {"type": "broadcast_end"})

            else:
                await safe_send(ws, {"type": "error", "message": f"Unknown type: {msg_type}"})

    except WebSocketDisconnect:
        pass
    finally:
        # stop ping task
        stop_ping.set()
        try:
            await ping_task
        except Exception:
            pass

        # remove connection
        connections.pop(client_id, None)

        meta = client_meta.pop(client_id, {"role": "", "room_id": ""})
        role = meta.get("role")
        room_id = meta.get("room_id")

        if room_id and room_id in rooms:
            if role == "listener":
                rooms[room_id]["listeners"].pop(client_id, None)

                # Optional: notify sender listener left (if you want cleanup on sender)
                sender_id = rooms[room_id].get("sender")
                if sender_id and sender_id in connections:
                    await safe_send(connections[sender_id], {"type": "listener_left", "listenerId": client_id})

            elif role == "sender":
                # End broadcast for all listeners
                for lws in list(rooms[room_id]["listeners"].values()):
                    await safe_send(lws, {"type": "broadcast_end"})
                rooms.pop(room_id, None)
