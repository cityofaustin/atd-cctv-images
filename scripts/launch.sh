#!/bin/bash
sudo docker run --name cctv-images \
  --network host \
  -d \
  --env-file /home/publisher/atd-cctv-images/env_file \
  -v /var/log/cctv-images:/app/cctv/_log \
  atddocker/atd-cctv-images cctv/process_images.py
