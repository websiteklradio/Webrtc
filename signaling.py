from fastapi import FastAPI, WebSocket
import uuid
import json

app = FastAPI()

sender = None
listeners = {}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global sender
    await ws.accept()

    client_id = str(uuid.uuid4())
    role = await ws.receive_text()

    if role == "sender":
        sender = ws
        print("Sender connected")

        while True:
            msg = await ws.receive_text()
            for l in listeners.values():
                await l.send_text(msg)

    else:
        listeners[client_id] = ws
        print("Listener connected:", client_id)

        try:
            while True:
                msg = await ws.receive_text()
                if sender:
                    data = json.loads(msg)
                    data["from"] = client_id
                    await sender.send_text(json.dumps(data))
        finally:
            listeners.pop(client_id, None)
