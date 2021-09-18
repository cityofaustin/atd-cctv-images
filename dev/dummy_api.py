""" This app can be used as a dummy camera endpoint for development purposes. You'll 
need to set the IP address of your Camera instances to this local host"""
import time
import random
from flask import Flask, send_from_directory
app = Flask(__name__)

@app.route('/')
def data():
    randnum = random.uniform(0,3)
    time.sleep(randnum)
    return send_from_directory("", "unavailable.jpeg")

if __name__ == "__main__":
    app.run()