#!/usr/bin/env python3
""" Class to download images from a single CCTV camera and upload them to S3 """
import asyncio
import datetime
import logging
import os
from time import mktime
from wsgiref.handlers import format_date_time

import aiobotocore
import httpx

CAMERA_USERNAME = os.getenv("CAMERA_USERNAME")
CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD")
WISENET_USERNAME = os.getenv("WISENET_USERNAME")
WISENET_PASSWORD = os.getenv("WISENET_PASSWORD")
BUCKET = os.getenv("BUCKET")
BUCKET_SUBDIR = "image"
SLEEP_SECONDS = 300
EXCEPTION_LIMIT = 5

logger = logging.getLogger("cctv_thumbnails")


class Camera(object):
    """Async processor to download/upload CCTV camera thumbnail images"""

    def __repr__(self):
        return f"<Camera '{self.ip}'>"

    def __init__(
        self,
        *,
        ip: str,
        id: int,
        model: str,
        fallback_img: bytes,
        exception_limit: int = EXCEPTION_LIMIT,
    ):
        """Initialize camera

        Args:
            ip (str): The camera's IP address
            id (int): The camera's unique ID.
            model (str): The camera's model name
            fallback_img (bytes): The image to upload if no image can be retrieved from the camera.
                Will only be uploaded once if successive attempts to download an image fail.
            exception_limit (int, optional): The max number of failed download attempts before
                permanently setting the camera into disabled state. Defaults to 3.

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
        self.fallback_img = fallback_img
        self.exception_limit = exception_limit

        # Digest auth for Hanwha AKA Wisenet cameras
        if self.model.lower() == "wisenet":
            self.auth = httpx.DigestAuth(WISENET_USERNAME, WISENET_PASSWORD)
        else:
            # authentication is build into the url for the rest of manufacturers
            self.auth = None

        """Camera state
            image (bytes): The image to upload. Reset to `None` before each new download attempt
            is_fallback_uploaded (bool): If the fallback image has been uploaded. Used to 
                avoid uploading the fallback image redundantly after repeated failuress. Reset
                to False after a successful image upload.
            url (str): The url endpoint of the camera
            exception_count (int): The number of successive exceptions which have raised while
                attempting to download/uploads. Reset after a successful download.
        """
        self.image = None
        self.is_fallback_uploaded = False
        self.url = self._build_url()
        self.exception_count = 0

    def _build_url(self):
        """Of the known camera models currently in use, type `advidia` has a distinct url
        pattern. Other models all use the same URL pattern"""
        if self.model.lower() == "advidia":
            auth = f"{CAMERA_USERNAME}:{CAMERA_PASSWORD}"
            return f"http://{auth}@{self.ip}/ISAPI/Streaming/channels/101/picture"
        elif self.model.lower() == "wisenet":
            return f"http://{self.ip}/stw-cgi/video.cgi?msubmenu=snapshot&action=view&Profile=1&Channel=0"
        else:
            return f"http://{self.ip}/jpeg?id=2"

    def _raise_exception(self, message):
        """Raise an exception after increassing exception_count"""
        self.exception_count + 1
        raise Exception(message)

    async def sleep(self):
        await asyncio.sleep(SLEEP_SECONDS)

    def is_disabled(self):
        return self.exception_count >= self.exception_limit

    def _expiration_timestamp(self):
        """Formats an http-timestamp to be used in the `Expires` header. This header
        is included in the S3 image upload, and is propagated through Cloudfront to
        Cloudfront client requests.
        """
        expires = datetime.datetime.now() + datetime.timedelta(0, SLEEP_SECONDS)
        stamp = mktime(expires.timetuple())
        return format_date_time(stamp)

    async def download(self, session: httpx.AsyncClient) -> bool:
        """Attempt to download a jpeg image from the camera.

        Args:
            session (httpx.AsyncClient): The client's httpx session

        Returns:
            bool: True if download successful, else False.
        """
        if self.is_disabled():
            return self._raise_exception(f"Disabled / at exception limit")

        # clear image if held from previous download
        self.image = None

        self.image = await self._download(session)

        # reset exception count
        self.exception_count = 0
        return True

    async def _download(self, session: httpx.AsyncClient) -> list:
        """Download an image from the camera.

        Args:
            session (httpx.AsyncClient): The httpx client session

        Returns:
            list: [response header, response content, response status, ressponse reason]
        """
        logger.debug(
            f"Downloading image from camera ID {self.id} {self.ip} {self.model}"
        )

        try:
            response = await session.get(self.url, auth=self.auth)
            response.raise_for_status()
        except httpx.RequestError as exc:
            return self._raise_exception(
                f"Request error to {exc.request.url}({exc.__class__})",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                # there's no reason to re-try 4xx errors, so trigger disabled state
                self.exception_count = self.exception_limit
            return self._raise_exception(
                f"Failed to fetch with status {exc.response.status_code} {exc.response.reason_phrase} ({exc.__class__})",
            )
        try:
            # note some cameras return a semicolon or charset definition in the content-type
            assert "image" in response.headers["content-type"]
        except AssertionError:
            # response is not an image
            return self._raise_exception(
                f"Unexpected Content-Type: {response.headers['content-type']}"
            )
        except (TypeError, KeyError) as e:
            # if the headers is of unexpected type or missing content-type header
            # unsure why this happens, but it has
            return self._raise_exception("Missing/invlaid header")
        return response.content

    async def upload(self, boto_client: aiobotocore.AioSession) -> bool:
        """Attempt to upload an image to S3. If self.image is None, the fallback image is uploaded.

        Args:
            boto_client (aiobotocore.AioSession): The boto3 client session.

        Returns:
            bool: True if successful else False
        """
        try:
            if not self.image and self.is_fallback_uploaded:
                """We want to avoid having stale images in S3. So the fallback image is uploaded if
                no image is available. If it's already uploaded, we don't need to upload it again
                """
                logger.debug(f"Skipping fallback image upload")
                return True

            logger.debug(f"Camera {self.id}: Uploading image")

            resp = await boto_client.put_object(
                Bucket=BUCKET,
                Key=f"{BUCKET_SUBDIR}/{self.id}.jpg",
                Body=self.image or self.fallback_img,
                ContentType="image/jpeg",
                Expires=self._expiration_timestamp(),
            )
            # reset the fallback image state if we've just uploaded a real image
            self.is_fallback_uploaded = True if (resp and not self.image) else False
            return True if resp else self._raise_exception("Unknown upload error")
        except Exception as e:
            return self._raise_exception(f"Unable to upload image with {str(e)}")
