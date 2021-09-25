#!/usr/bin/env python3
""" Async fetches traffic cctv image thumbnails and uploads to S3.

docker run --network host -it --rm --env-file env_file -v /home/publisher/atd-cctv-thumbnails:/app atddocker/atd-cctv-thumbnails /bin/bash
docker run --network host -it --rm --env-file env_file -v /home/publisher/atd-cctv-thumbnails:/app atddocker/atd-cctv-thumbnails ./process_images.py
sudo docker run --name cctv-images --network host -it --rm  --env-file /home/publisher/atd-cctv-images/env_file -v /home/publisher/atd-cctv-images:/app atddocker/atd-cctv-images cctv/process_images.py

"""
import argparse
import asyncio
import logging
import logging.handlers
import os
import random
import sys

from asyncio.unix_events import _compute_returncode

import aiohttp
import aiobotocore
import knackpy

from camera import Camera

# environment
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
KNACK_CONTAINER = os.getenv("KNACK_CONTAINER")
KNACK_API_KEY = os.getenv("KNACK_API_KEY")
KNACK_APP_ID = os.getenv("KNACK_APP_ID")

# config
LOG_DIR = "_log"
IP_FIELD = "field_638"
ID_FIELD = "field_947"
MODEL_FIELD = "field_639"
FALLBACK_IMG_NAME = "unavailable.jpg"
TIMEOUT_DEFAULT = 60
INITIAL_MAX_RANDOM_SLEEP = 300

def get_camera_records():
    """Download camera records from Knack app.

    Returns:
        list: list of knackpy.Records
    """
    logger.debug("Getting cameras from Knack...")
    filters = {
        "match": "and",
        "rules": [
            {"field": IP_FIELD, "operator": "is not blank"},
            {"field": ID_FIELD, "operator": "is not blank"},
            {"field": MODEL_FIELD, "operator": "is not blank"},
        ],
    }
    app = knackpy.App(app_id=KNACK_APP_ID, api_key=KNACK_API_KEY)
    return app.get(KNACK_CONTAINER, filters=filters)


def create_camera(record, fallback_img):
    """Create Camera instances.

    Args:
        record (knackpy.Record): A knackpy.Record of the camera asset data

    Returns:
        Camera: Camera instance or none if insufficient data available
    """
    ip = record.get(IP_FIELD)
    camera_id = record.get(ID_FIELD)
    model = record.get(MODEL_FIELD)
    return Camera(ip=ip, id=camera_id, model=model, fallback_img=fallback_img)


async def worker(camera: Camera, session, boto_client):
    """ Looping task-worker which manges i/o for a Camera instance

    Exceptions must caught liberally to ensure that a worker does not reach an unhandled
    exception state—which would block the event loop and stop all other workers.

    Args:
        camera (Camera): The camera instance
        session (aiohttp.ClientSession): The aiohttp session to use when fetching from cameras
        boto_client (aiobotocore.Session): The (aio)boto3 session to upload images

    Returns:
        None
    """
    # apply an initial random sleep to avoid overloading CPU with concurrent i/o on init
    await asyncio.sleep(random.uniform(0, INITIAL_MAX_RANDOM_SLEEP))
    while True:
        if camera.is_disabled():
            logger.debug(f"{camera.id} is disabled")
            # terminate work if camera reaches disabled state
            return

        try:
            await camera.download(session)
        except Exception as e:
            logger.error(f"Camera {camera.id}: download: {e.__class__} {str(e)}")

        try:
            # we upload regardless of if a new image was downloaded
            # camera state determines if the fallback image should be uploaded
            await camera.upload(boto_client)
        except Exception as e:
            logger.error(f"Camera {camera.id}: upload: {str(e)}")

        logger.debug(f"done with {camera.id}")
        
        # sleep until
        await camera.sleep()


def load_fallback_img(fname):
    dirname = os.path.dirname(__file__)
    filepath = os.path.join(dirname, fname)
    with open(filepath, "rb") as fin:
        return fin.read()


async def main(timeout):
    """Initates the infinite fetch/upload loop.
    Note that Knack camera asset records are only fetched once. This script must be restarted in order
    to check for new/modified cameras.

    Args:
        timeout (int): The aiohttp session timeout (applied when downloading, not uploading images)
    """
    fallback_img = load_fallback_img(FALLBACK_IMG_NAME)
    cameras_knack = get_camera_records()
    cameras = [create_camera(record, fallback_img) for record in cameras_knack]

    tasks = []

    timeout = aiohttp.ClientTimeout(total=timeout)
    session = aiobotocore.session.get_session()

    # wrap all connections in sessions, which are expensive to create
    async with session.create_client(
        "s3",
        region_name="us-east-2",
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
    ) as boto_client:

        async with aiohttp.ClientSession(timeout=timeout) as session:
            # create workers and tie them to the queue
            for camera in cameras:
                task_worker = worker(camera, session, boto_client)
                task = asyncio.create_task(task_worker)
                tasks.append(task)

            # Wait until all worker tasks are cancelled.
            await asyncio.gather(*tasks, return_exceptions=True)


def get_logger(name, log_dir_path, level):
    """Return a module logger that streams to stdout and to rotating file"""
    logger = logging.getLogger(name)
    formatter = logging.Formatter(fmt="%(asctime)s %(levelname)s: %(message)s")
    handler_stream = logging.StreamHandler(stream=sys.stdout)
    handler_stream.setFormatter(formatter)
    logger.addHandler(handler_stream)
    handler_file = logging.handlers.RotatingFileHandler(
        f"{log_dir_path}/cctv.log", maxBytes=2000000, backupCount=5
    )
    handler_file.setFormatter(formatter)
    logger.addHandler(handler_file)
    logger.setLevel(level)
    return logger


if __name__ == "__main__":
    dirname = os.path.dirname(__file__)
    log_dir_path = os.path.join(dirname, LOG_DIR)
    os.makedirs(log_dir_path, exist_ok=True)
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=TIMEOUT_DEFAULT,
        help=f"timeout seconds when connecting to cctv (default: {TIMEOUT_DEFAULT})",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help=f"Sets logger to DEBUG level",
    )
    args = parser.parse_args()

    logger = get_logger(
        "cctv_thumbnails",
        log_dir_path,
        level=logging.DEBUG if args.verbose else logging.ERROR,
    )
    asyncio.run(main(args.timeout))
