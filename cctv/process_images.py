#!/usr/bin/env python3
""" Async fetches traffic cctv image thumbnails and uploads to S3"""
import argparse
import asyncio
import logging
import logging.handlers
import os
import random
import sys

import httpx
import aiobotocore
import knackpy

from camera import Camera
from camera import SLEEP_SECONDS

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
DISABLE_PUBLISH_FIELD = "field_1866"
FALLBACK_IMG_NAME = "unavailable.jpg"
TIMEOUT_DEFAULT = 180
INITIAL_MAX_RANDOM_SLEEP = 300


def get_camera_records(app, get_disabled=False):
    """Download camera records from Knack app.
    Args:
        app: knackpy app object
        get_disabled: if True, return disabled camera records instead
    Returns:
        list: list of knackpy.Records
    """

    if get_disabled:
        logger.debug("Fetching disabled cameras from Knack...")
        filters = {
            "match": "and",
            "rules": [
                {"field": DISABLE_PUBLISH_FIELD, "operator": "is", "value": True},
            ],
        }
    else:
        logger.debug("Fetching cameras from Knack...")
        filters = {
            "match": "and",
            "rules": [
                {"field": IP_FIELD, "operator": "is not blank"},
                {"field": ID_FIELD, "operator": "is not blank"},
                {"field": MODEL_FIELD, "operator": "is not blank"},
                {"field": DISABLE_PUBLISH_FIELD, "operator": "is not", "value": True},
            ],
        }
    return app.get(KNACK_CONTAINER, filters=filters, refresh=True)


def create_camera(record, fallback_img):
    """Create Camera instances.

    Args:
        record (knackpy.Record): A knackpy.Record of the camera asset data
        fallback_img (bytes): The image to be uploaded if no image can be downloaded. The
        fallback image prevents stale images from persitsing in the S3 store.
    Returns:
        Camera: Camera instance

    Raises:
        ValueError: raised by <Camera > if not ip, id, or model
    """
    ip = record.get(IP_FIELD).strip()
    camera_id = record.get(ID_FIELD)
    model = record.get(MODEL_FIELD)
    return Camera(ip=ip, id=camera_id, model=model, fallback_img=fallback_img)


async def worker(
    camera: Camera,
    session: httpx.AsyncClient,
    boto_client: aiobotocore.session.AioSession,
):
    """Task-worker which manages i/o for a Camera instance. runs on an infinite loop until a
    camera becomes disabled, which happens if a camera upload/download fails repeatedly up
    to its `exception_limit`.

    Exceptions must caught liberally to ensure that a worker does not reach an unhandled
    exception state—which would block the event loop and stop all other workers.

    Args:
        camera (Camera): The camera instance
        session (httpx.AsyncClient): The httpx session to use when fetching from cameras
        boto_client (aiobotocore.session.AioSession): The (aio)boto3 session to upload images

    Returns:
        None
    """
    # apply an initial random sleep to avoid overloading CPU with concurrent i/o on init
    await asyncio.sleep(random.uniform(0, INITIAL_MAX_RANDOM_SLEEP))
    while True:
        if camera.is_disabled():
            logger.debug(f"{camera.id} is disabled")
            # overwrite stale image with placeholder
            await camera.upload(boto_client)
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
        # pause for sleep duration
        await camera.sleep()


async def update_camera_stack(app, cameras, session, boto_client):
    """
    Checks Knack to see if any cameras have been disabled or re-enabled by TPW staff.
    Args:
        app: knackpy app object
        cameras: list of Camera objects
        session (httpx.AsyncClient): The httpx session to use when fetching from cameras
        boto_client (aiobotocore.session.AioSession): The (aio)boto3 session to upload images
    """
    fallback_img = load_fallback_img(FALLBACK_IMG_NAME)
    await asyncio.sleep(random.uniform(0, INITIAL_MAX_RANDOM_SLEEP))
    while True:
        logger.debug("Checking for disabled cameras from Knack...")

        try:
            cameras_knack = get_camera_records(app, get_disabled=True)
        except Exception as e:
            logger.debug("Error trying to fetch camera data from Knack, skipping updating.")
            await asyncio.sleep(SLEEP_SECONDS)
            continue

        # Checking our published cameras to see if they were disabled
        for cam_data in cameras_knack:
            if cam_data.get(DISABLE_PUBLISH_FIELD):
                cam_id = cam_data.get(ID_FIELD)
                for camera in cameras:
                    if camera.id == cam_id:
                        camera.disable_camera()
                        logger.debug(f"Camera {cam_id} was disabled by Knack.")

        # refreshing our list of cameras
        cameras = [camera for camera in cameras if not camera.is_disabled()]

        # Now, check for cameras that were recently added or enabled
        cameras_knack = get_camera_records(app, get_disabled=False)
        cam_ids = [camera.id for camera in cameras]
        for cam_data in cameras_knack:
            cam_id = cam_data.get(ID_FIELD)
            if cam_id not in cam_ids:
                logger.debug(f"Camera {cam_id} was re-enabled by Knack.")
                cam_obj = create_camera(cam_data, fallback_img)
                cam_worker = worker(cam_obj, session, boto_client)
                cam_task = asyncio.create_task(cam_worker)
                event_loop = asyncio.get_event_loop()
                asyncio.ensure_future(cam_task, loop=event_loop)
                cameras.append(cam_obj)

        await asyncio.sleep(SLEEP_SECONDS)


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
        timeout (int): The httpx session timeout (applied when downloading, not uploading images)
    """
    fallback_img = load_fallback_img(FALLBACK_IMG_NAME)
    app = knackpy.App(app_id=KNACK_APP_ID, api_key=KNACK_API_KEY)
    cameras_knack = get_camera_records(app)
    cameras = [create_camera(record, fallback_img) for record in cameras_knack]
    tasks = []

    # wrap all connections in a single context, which is expensive to create
    timeout = httpx.Timeout(timeout)
    session = aiobotocore.session.get_session()

    async with session.create_client(
        "s3",
        region_name="us-east-2",
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
    ) as boto_client:
        async with httpx.AsyncClient(timeout=timeout) as session:
            # create workers and tie them to tasks
            for camera in cameras:
                task_worker = worker(camera, session, boto_client)
                task = asyncio.create_task(task_worker)
                tasks.append(task)
            # Task to check knack to see if any cameras were added or removed
            knack_worker = update_camera_stack(app, cameras, session, boto_client)
            knack_task = asyncio.create_task(knack_worker)
            tasks.append(knack_task)
            # Concurrently run all tasks until they complete
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
        "-v",
        "--verbose",
        action="store_true",
        help=f"Sets logger to DEBUG level",
    )
    args = parser.parse_args()

    logger = get_logger(
        "cctv_thumbnails",
        log_dir_path,
        level=logging.DEBUG if args.verbose else logging.ERROR,
    )
    asyncio.run(main(args.timeout))
