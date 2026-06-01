import asyncio
import json
import os
import socket
from pathlib import Path

# --- Constants ---
UDS_PATH = "/tmp/nuke_spike_bridge.sock"

# --- Simulation Logic ---

async def run_worker_spike():
    """
    Simulates a Worker process.
    It connects to the Supervisor via UDS and sends a 'broadcast' event.
    """
    print("[Worker] Starting spike...")
    await asyncio.sleep(1) # Wait for supervisor to start
    
    try:
        reader, writer = await asyncio.open_unix_connection(UDS_PATH)
        print("[Worker] Connected to Supervisor UDS.")
        
        # Simulate a Bus event (e.g., Bot is typing)
        event = {
            "type": "broadcast",
            "group_id": 1,
            "payload": {
                "type": "typing",
                "sender_name": "DevBot",
                "content": "I am thinking about the spike..."
            }
        }
        
        message = json.dumps(event).encode() + b"\n"
        writer.write(message)
        await writer.drain()
        print(f"[Worker] Sent event: {event['payload']['type']}")
        
        # Keep connection open for a bit
        await asyncio.sleep(2)
        writer.close()
        await writer.wait_closed()
        print("[Worker] Spike finished.")
        
    except Exception as e:
        print(f"[Worker] Error: {e}")

async def run_supervisor_spike():
    """
    Simulates the Supervisor process.
    It listens on a UDS socket and 'forwards' messages to a simulated WS handler.
    """
    if os.path.exists(UDS_PATH):
        os.remove(UDS_PATH)
        
    print("[Supervisor] Starting UDS server...")
    
    async def handle_client(reader, writer):
        addr = writer.get_extra_info('peername')
        print(f"[Supervisor] New connection from Worker.")
        
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                
                data = json.loads(line.decode().strip())
                if data.get("type") == "broadcast":
                    group_id = data.get("group_id")
                    payload = data.get("payload")
                    
                    # SIMULATED WEB SOCKET BROADCAST
                    print(f"🚀 [Supervisor -> WebSocket] Forwarding to Group {group_id}: {payload}")
                
        except Exception as e:
            print(f"[Supervisor] Handler error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
            print("[Supervisor] Worker disconnected.")

    server = await asyncio.start_unix_server(handle_client, path=UDS_PATH)
    
    async with server:
        await server.serve_forever()

async def main():
    # Run both in the same event loop for the spike demonstration
    try:
        # Task for supervisor
        sup_task = asyncio.create_task(run_supervisor_spike())
        # Task for worker
        work_task = asyncio.create_task(run_worker_spike())
        
        # Wait for worker to finish
        await work_task
        # Cancel supervisor after worker is done
        sup_task.cancel()
        try:
            await sup_task
        except asyncio.CancelledError:
            pass
            
    finally:
        if os.path.exists(UDS_PATH):
            os.remove(UDS_PATH)

if __name__ == "__main__":
    asyncio.run(main())
