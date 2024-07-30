import asyncio
import base64
import json
import os
import time
import logging
from datetime import datetime
from PIL import Image
import io
import websockets
from opcua import Client, ua
from threading import Timer
import threading
import time

# Define custom logging level
IMPORTANT = 25
logging.addLevelName(IMPORTANT, "IMPORTANT")
logging.Logger.important = lambda self, message, *args, **kws: self._log(IMPORTANT, message, args, **kws) if self.isEnabledFor(IMPORTANT) else None
logging.basicConfig(level=IMPORTANT, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration and Constants
OPC_SERVER_URL = os.getenv('OPC_SERVER_URL', 'opc.tcp://10.15.160.149:49312')
TAG_NAME = os.getenv('TAG_NAME', 'ns=2;s=BRASSAGEM.PLC1.WHIRLPOOL.SORBA.PHASE')
PRODUCT_TAG_NAME = os.getenv('PRODUCT_TAG_NAME', 'ns=2;s=BRASSAGEM.PLC1.WHIRLPOOL.SORBA.PROGNO')
EQUIPMENT = os.getenv('EQUIPMENT', 'DECANTADOR')
VALID_STEPS = os.getenv('VALID_STEPS', "1;0;1,2;0;1,3;0;1,4;0;1,5;0;1,6;0;1,12;30;2")
NUMBER_OF_PICTURES = int(os.getenv('NUMBER_OF_PICTURES', 5))
BASE_IMAGE_SAVE_PATH = './data'

# Ensure directories exist
def ensure_directory(path):
    if not os.path.exists(path):
        os.makedirs(path)

# Global variables
latest_image = None  # Latest image buffer

# WebSocket handling
async def websocket_handler(websocket, path):
    global latest_image
    while True:
        data = await websocket.recv()
        try:
            data = json.loads(data)
            image_data = data['data']
            img_bytes = base64.b64decode(image_data)
            latest_image = Image.open(io.BytesIO(img_bytes))
            logging.getLogger().important("Image received and updated.")
        except Exception as e:
            logging.getLogger().important(f"Failed to process received image: {e}")

# Picture taking mechanism, integrated with existing process
def take_pictures(step, is_product_change=False):
    directory_suffix = "CIP" if is_product_change else step
    directory_path = os.path.join(BASE_IMAGE_SAVE_PATH, EQUIPMENT, directory_suffix)
    ensure_directory(directory_path)

    global latest_image
    if latest_image is None:
        logging.getLogger().important("No image available to save.")
        return

    for i in range(NUMBER_OF_PICTURES):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        image_path = os.path.join(directory_path, f'{timestamp}_{i}.png')
        try:
            latest_image.save(image_path)
            logging.getLogger().important(f"Image successfully saved: {image_path}")
        except Exception as e:
            logging.getLogger().important(f"Failed to save image: {e}")
        time.sleep(1)  # Sleep for pacing image saves

# Existing application logic with some modifications
def parse_valid_steps(config):
    steps = {}
    entries = config.split(',')
    for entry in entries:
        parts = entry.split(';')
        step = f"{float(parts[0]):.1f}"  # Format with one decimal place
        delay = float(parts[1])
        strategy = int(parts[2])
        steps[step] = {'delay': delay, 'strategy': strategy}
    return steps

valid_steps = parse_valid_steps(VALID_STEPS)
logging.getLogger().important(f"Valid steps loaded: {valid_steps}")

class SubHandler(object):
    def __init__(self):
        self.last_value = None
        self.last_product_value = None
        self.active_timer = None
        self.last_strategy = None
        self.initial_step_change = False  # Flag to check if initial step change has occurred
        self.initial_product_change = False  # Flag to check if initial product change has occurred

    def handle_value_change(self, new_value):
        print("Handling value change for:", new_value)
        if self.active_timer:
            self.active_timer.cancel()
            self.active_timer = None
            logging.getLogger().important("Cancelled previous timer due to new valid step.")

        step_key = f"{float(new_value):.1f}"
        step_info = valid_steps.get(step_key)
        print("Step info:", step_info)

        if not self.initial_step_change:
            self.initial_step_change = True  # Mark the first change
            self.last_value = new_value
            self.last_strategy = step_info['strategy'] if step_info else None
            return  # Skip processing for the first change

        # Check if exiting from Strategy 2
        if self.last_strategy == 2:
            if not step_info or step_info['strategy'] != 2:
                take_pictures(str(self.last_value))
            elif step_info['strategy'] == 2 and step_key != f"{float(self.last_value):.1f}":
                # Additional condition to handle transition between different Strategy 2 steps
                take_pictures(str(self.last_value))

        if step_info:
            strategy = step_info['strategy']
            delay = step_info['delay']
            if strategy == 1:
                if delay > 0:
                    self.active_timer = Timer(delay, lambda: take_pictures(step_key))
                    self.active_timer.start()
                else:
                    take_pictures(step_key)
            elif strategy == 2:
                # Setup or placeholder for specific action when entering a Strategy 2 step
                # No action needed here if not entering from another Strategy 2 step
                pass
            elif strategy == 3:
                self.start_continuous_capture(step_key, delay)

        self.last_value = new_value
        self.last_strategy = step_info['strategy'] if step_info else None

    def start_continuous_capture(self, step, interval):
        def capture():
            print(self.last_value)
            print(step)
            #if self.last_value == step:  # Continue capturing if the step hasn't changed
            take_pictures(step)
            self.active_timer = Timer(interval, capture)
            self.active_timer.start()

        capture()

    def handle_product_change(self, product_value):
        if not self.initial_product_change:  # Check if it's the first product change
            self.initial_product_change = True
            self.last_product_value = product_value  # Set initial product value
            return  # Skip further processing until the next product change

        # Now handle changes only if last_product_value is not None
        if self.last_product_value is not None and product_value >= 0 and self.last_product_value < 0:
            take_pictures("any_value", is_product_change=True)

        self.last_product_value = product_value  # Update last_product_value for next change

    def datachange_notification(self, node, val, data):
        new_value = round(float(val), 1)
        if str(node) == PRODUCT_TAG_NAME:
            self.handle_product_change(new_value)
        else:
            logging.getLogger().important(f"Data change on {node}: New value = {new_value}")
            self.handle_value_change(new_value)

def connect_to_opcua():
    while True:
        client = Client(OPC_SERVER_URL)
        try:
            client.connect()
            logging.info(f"Connected to {OPC_SERVER_URL}")
            tag_node = client.get_node(TAG_NAME)
            product_node = client.get_node(PRODUCT_TAG_NAME)
            handler = SubHandler()
            sub = client.create_subscription(500, handler)
            sub.subscribe_data_change(tag_node)
            sub.subscribe_data_change(product_node)
            logging.info("Subscription created, waiting for events...")

            while True:
                try:
                    # Periodically check if the connection is still alive
                    tag_node.get_value()
                    time.sleep(1)
                except ua.UaStatusCodeError:
                    logging.error("Lost connection to OPC UA server. Trying to reconnect...")
                    break
        except Exception as e:
            logging.exception(f"An error occurred: {e}")
            time.sleep(15)  # Wait for 15 seconds before trying to reconnect
        finally:
            try:
                client.disconnect()
                logging.info("Client disconnected.")
            except Exception as e:
                logging.exception("Error during disconnection: " + str(e))

async def websocket_server():
    async with websockets.serve(websocket_handler, "0.0.0.0", 8000):
        await asyncio.Future()  # This will run forever

def main():
    # Thread for the OPC UA client
    opcua_thread = threading.Thread(target=connect_to_opcua)
    opcua_thread.start()

    # Run the asyncio event loop for the WebSocket server
    asyncio.run(websocket_server())

if __name__ == '__main__':
    main()

