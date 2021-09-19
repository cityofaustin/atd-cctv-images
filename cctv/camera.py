#!/usr/bin/env python3
""" Class to download images from a single CCTV camera and upload them to S3 """
import asyncio
from datetime import datetime
import io
import logging
import random
import os
from types import TracebackType

import aiobotocore
import aiohttp

CAMERA_USERNAME = os.getenv("CAMERA_USERNAME")
CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD")
BUCKET = os.getenv("BUCKET")

logger = logging.getLogger("cctv_thumbnails")


class Camera(object):
    """Async processor to fetch CCTV camera thumbnail images."""

    def __repr__(self):
        return f"<Camera {self.ip}>"

    def __init__(self, *, ip: str, id: int, model: str, fallback_img, download_error_limit=3):
        """Initialize camera

        Args:
            ip (str): The camera's IP address
            id (int): The camera's unique ID.
            model (str): The camera's model name
            download_error_limit (int, optional): The max number of failed download attempts before
            permanently setting the camera into error state. Defaults to 3.

        Raises:
            ValueError: If any of ip, id, or model are falsey
        """
        try:
            assert ip and id and model
        except AssertionError:
            raise ValueError("Insufficient args supplied to Camera instance")

        self.id = id
        self.model = model
        self.ip = ip
        self.image = None
        self.is_fallback_uploaded = False
        self.fallback_img = fallback_img
        self.url = self._build_url()
        self.download_error_count = 0
        self.download_error_limit = download_error_limit

    def _error_state(self):
        return self.download_error_count >= self.download_error_limit

    def _build_url(self):
        """Of the known camera models currently in use, type `advidia` has a distinct url
        pattern. Other models all use the same URL pattern"""
        if self.model.lower() == "advidia":
            auth = f"{CAMERA_USERNAME}:{CAMERA_PASSWORD}"
            return f"http://{auth}@{self.ip}/ISAPI/Streaming/channels/101/picture"
        else:
            return f"http://{self.ip}/jpeg?id=2"

    async def download(self, session: aiohttp.ClientSession) -> bool:
        """Attempt to download a jpeg image from the camera.

        Args:
            session (aiohttp.ClientSession): The client's http session

        Returns:
            bool: True if download successful, else False.
        """
        if self._error_state():
            logger.debug(f"Camera {self.id} is in error state and will be skpped")
            return False

        # clear image if held from previous download
        self.image = None

        try:
            headers, content, status, reason = await self._download(session)
        except Exception as e:
            # timeouts and connection errors caught here
            logger.error(f"Failed to fetch camera ID {self.id}: {e.__class__}")
            self.download_error_count += 1
            return False

        try:
            assert status < 400
        except AssertionError:
            logger.error(
                f"Failed to fetch camera ID {self.id} with status {status} {reason}"
            )
            if status < 500:
                # there's no reason to re-try 4xx errors, so trigger error state
                self.download_error_count = self.download_error_limit
            return False

        try:
            # note some cameras return a semicolon or charset definition in the content-type
            assert "image" in headers["content-type"]
        except AssertionError:
            # response is not an image
            logger.error(
                f"Unexpected Content-Type for camera ID {self.id}: {headers['content-type']}"
            )
            self.download_error_count += 1
            return False

        except (TypeError, KeyError):
            # if the headers is of unexpected type or missing content-type header
            # unsure why this happens, but it has
            logger.error(f"Missing/invlaid header for camera ID {self.id}")
            self.download_error_count += 1
            return False

        self.image = content
        self.download_error_count = 0
        return True

    async def _download(self, session: aiohttp.ClientSession) -> list:
        """Download an image from the camera.

        Args:
            session (aiohttp.ClientSession): The http client session

        Returns:
            list: [response header, response content, response status, ressponse reason]
        """
        logger.debug(
            f"Downloading image from camera ID {self.id} {self.ip} {self.model}"
        )
        async with session.get(self.url) as resp:
            content = await resp.content.read()
            return [resp.headers, content, resp.status, resp.reason]

    async def upload(self, boto_client: aiobotocore.AioSession) -> bool:
        """Attempt to upload an image to S3. If self.image is None, the fallback image is uploaded.

        Args:
            boto_client (aiobotocore.AioSession): The boto3 client session.

        Returns:
            bool: True if successful else False
        """
        try:
            if not self.image and self.is_fallback_uploaded:
                """ We want to avoid having stale images in S3. So the fallback image is uploaded if
                no image is available. If it's already uploaded, we don't need to upload it again"""
                logger.debug(f"Skipping fallback image for camera ID {self.id}")
                return True
            
            resp = await boto_client.put_object(
                Bucket=BUCKET, Key=f"{self.id}.jpg", Body=self.image or self.fallback_img
            )
            # reset the fallback image state if we've just uploaded a real image
            self.is_fallback_uploaded = True if (resp and not self.image) else False
            return True if resp else False
        except Exception as e:
            logger.error(
                f"Unable to upload image for camera ID {self.id}: {e.__class__}"
            )
            return False
