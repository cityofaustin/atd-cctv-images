#!/bin/bash
if [ ! "$(sudo docker ps -a | grep cctv-images)" ]; then
    # container doesn't exist; create and run it
    sudo docker run --name cctv-images --network host -d --env-file /home/publisher/atd-cctv-images/env_file -v /home/publisher/atd-cctv-images:/app atddocker/atd-cctv-images cctv/process_images.py
elif [ ! "$(sudo docker ps | grep cctv-images)" ]; then
    # container exists but is not running; restart it
    sudo docker restart "cctv-images"
fi
