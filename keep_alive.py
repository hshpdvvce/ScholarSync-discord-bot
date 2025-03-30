from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running!"

def run():
    app.run(host='0.0.0.0', port=5000)

def keep_alive():
    server = Thread(target=run)
    server.daemon = True  # This ensures the thread will close when the main program ends
    server.start()