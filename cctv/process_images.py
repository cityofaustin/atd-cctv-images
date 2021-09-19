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
import sys

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
NUM_WORKERS_DEFAULT = 30
TIMEOUT_DEFAULT = 30
FALLBACK_IMG_NAME = "unavailable.jpg"


def get_camera_records():
    """Download camera records from Knack app.

    Returns:
        list: list of knackpy.Records
    """
    logger.debug("Getting cameras from Knack...")
    app = knackpy.App(app_id=KNACK_APP_ID, api_key=KNACK_API_KEY)
    return app.get(KNACK_CONTAINER)


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
    if not ip or not camera_id or not model:
        logger.warning("Unable to create camera due to missing id, ip, or model")
        return None
    return Camera(ip=ip, id=camera_id, model=model, fallback_img=fallback_img)


async def worker(worker_id, queue, session, boto_client):
    """Worker which interacts with Camera task queue to fetch and upload images. The worker
    pulls the next Camera in the queue, attempts to process it, and returns it to the queue
    for future processing, creating an infinite loop of worker tasks processed in first in,
    first out order.

    Exceptions must caught liberally to ensure that a worker does not reach an unhandled
    exception state—which would block the event loop and stop all other workers.

    Args:
        worker_id (int): Unique/arbitrary ID of worker. Used merely for debug.
        queue (asyncio.Queue): The queue instance
        session (aiohttp.ClientSession): The http session to use when fetching from cameras
        boto_client (aiobotocore.Session): The (aio)boto3 session to use when

    Returns:
        None
    """
    while True:
        try:
            camera = await queue.get()
        except asyncio.QueueEmpty:
            """
            this can't happen, given that workers place cameras back in the queue when done,
            and in any case an uncaught QueueEmpty would just terminate the worker. we're
            just being verbose
            """
            return

        try:
            await camera.download(session)
        except Exception as e:
            """
            we don't know exactly what exceptions we'll need to handle. we have catches
            in <Camera> to add detail. we must always log and move past exceptions because
            they will block other workers
            """
            logger.error(
                f"MYSTERY FAILLLLLLED to fetch camera ID {camera.id}: {e.__class__}"
            )

        try:
            await camera.upload(boto_client)
        except Exception as e:
            logger.error(
                f"MYSTERY FAILLLLLLED UPLOAD camera ID {camera.id}: {e.__class__}"
            )

        # success or fail, the task is complete
        queue.task_done()
        logger.debug(f"Worker {worker_id} done with {camera.id}")
        # send the camera to end of the queue
        await queue.put(camera)


def load_fallback_img(fname):
    dirname = os.path.dirname(__file__)
    filepath = os.path.join(dirname, fname)
    with open(filepath, "rb") as fin:
        return fin.read()


async def main(max_workers, timeout):
    """Initates the infinite fetch/upload loop.
    Note that Knack camera asset records are only fetched once. This script must be restarted in order
    to check for new/modified cameras.

    Args:
        max_workers (int): The number of concurrent workers
        timeout (int): The aiohttp session timeout (applied when downloading, not uploading images)
    """
    fallback_img = load_fallback_img(FALLBACK_IMG_NAME)
    cameras_knack = get_camera_records()
    cameras = [create_camera(record, fallback_img) for record in cameras_knack]

    queue = asyncio.Queue()

    # initialize the to-do queue
    for cam in cameras:
        if cam:
            await queue.put(cam)

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
            for i in range(max_workers):
                task_worker = worker(str(i), queue, session, boto_client)
                task = asyncio.create_task(task_worker)
                tasks.append(task)

            # run tasks until the queue is empty
            await queue.join()

    # Clear our tasks: the program will not end until send the task-workers home
    for task in tasks:
        task.cancel()

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
        "-w",
        "--max_workers",
        type=int,
        default=NUM_WORKERS_DEFAULT,
        help=f"# of concurrent workers (default: {NUM_WORKERS_DEFAULT})",
    )

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
    asyncio.run(main(args.max_workers, args.timeout))
