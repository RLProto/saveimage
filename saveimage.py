import asyncio
import base64
import json
from datetime import datetime
from PIL import Image
import io
import websockets

async def receive_image(websocket, path):
    while True:
        data = await websocket.recv()
        if data:
            try:
                # Assuming data is JSON with a base64-encoded image
                data = json.loads(data)
                image_data = data['data']
                # Decode the base64 image
                img_bytes = base64.b64decode(image_data)
                # Convert bytes to an image and save it
                image = Image.open(io.BytesIO(img_bytes))
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                image.save(f'received_image_{timestamp}.png')  # Save image with timestamp
                print(f"Image saved as received_image_{timestamp}.png")
            except Exception as e:
                print(f"Error processing or saving image: {e}")

async def main():
    async with websockets.serve(receive_image, "localhost", 8000):
        print("WebSocket Server Started at ws://localhost:8000")
        await asyncio.Future()  # Run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Server shutdown.")