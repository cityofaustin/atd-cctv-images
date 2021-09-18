#!/bin/bash
# restart scraper to allow it to fetch any new sensor records that may have bene created
# run this nightly
sudo docker restart "cctv-images"
