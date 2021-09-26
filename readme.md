# atd-cctv-images

## About

Austin Transportation operates nearly 1,000 traffic cameras, which are used by the City's [Mobility Management Center](https://www.austintexas.gov/department/arterial-management) to monitor and address traffic issues in real time. The camera feeds are not recorded, and they are not used for law enforcement activites.

Although operations staff have access to live camera feeds, we have found it valuable to make intermittently-updated camera images available at a public HTTP endpoint. These images are incorporated into our own [operations dashboards](https://data.mobiltiy.austin.gov) and serve as a resource to the public, researchers, and 3rd-party services.

This module fetches thumbnail images from the traffic cameras network and uploads them to a cloud-fronted AWS S3 bucket. 

## Resources

- Images are available at `https://cctv.austinmobility.io/image/< camera-id >.jpg`

- The [public traffic camera dataset](https://data.austintexas.gov/Transportation-and-Mobility/Traffic-Cameras/b4k4-adkb) provides a listing of all available cameras

- The [traffic cameras dashboards(https://data.mobiltiy.austin.gov) is a map for searching/viewing cameras

## Design

The image processing is designed to be resilient to various connectivty and interface issues related to external factors such as power loss, device failure, and device misconfiguration. The processing is further complicated by the fact that multiple makes and models of CCTV cameras are used on the network, each with their own API. Running asynchronous Python adds layer of complication because an uncaught worker failure can potentially hault all concurrent tasks.

The code relies on Python's [asyncio](https://docs.python.org/3/library/asyncio.html) ecosystem to achieve fast processing of hundreds of images per minute. The primary script--`process_images.py`--initaties concurrent, looping [`tasks`](https://docs.python.org/3/library/asyncio-task.html#task-object) which fetch and upload images from cameras.

Camera IDs, IP addresses, and model info are managed in a Knack application. These records are fetched once on initialization, and the script musts be restarted if camera records need to be refreshed.

Each camera-task loops infinitely, sleeping for 5 minutes between each loop. When a new image is uploaded, it's [`Expires`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Expires) header is set to five minutes into the future, which allows AWS Cloudfront to appropriately cache an image until a new image should be available. If a camera fails to download a new image, `unavailable.jpg` will be uploaded as fallback image. This ensures that stale images do not persist in the bucket. If a camera repeatedly fails to fetch/upload a new image, the camera's infinite loop is terminated, and no further processing will occur for that camera until the script is restarted.

## Deployment

Configure environmental variables:

- `AWS_ACCESS_KEY_ID`: The AWS access key ID
- `AWS_SECRET_ACCESS_KEY`: The AWSS access key
- `BUCKET`: The AWS destination bucket
- `CAMERA_USERNAME`: The CCTV camera username
- `CAMERA_PASSWORD`: The CCTV camera password
- `KNACK_APP_ID`: The Knack APP ID which hosts the camera asset records
- `KNACK_API_KEY`: The knack APP API key
- `KNACK_CONTAINER`: The Knack container (aka, view ID) which exposes the asset recordss

The `launch.sh` script available in `/scripts` will launch a docker container that initates `cctv/processs-images.py`. It also does the work of loading environmental variabless and mounting the log directory:

```shell
sudo docker run --name cctv-images \
  --network host \
  -d \
  --env-file /home/publisher/atd-cctv-images/env_file \
  -v /var/log/cctv-images:/app/cctv/_log \
  atddocker/atd-cctv-images cctv/process_images.py
```

Once launched, a `cron` task can be deployed to run `/scripts/restart.sh`, which willl periodically restart the container and ensure that camera records are fetched.

Logs are configured to rotate at `1mb`. If you've mounted the log directory to the container, you can tail them like so.

```
$ tail -f /var/log/cctv-images/cctv.log 
```

## Development and CI

`/dev/dummy_api.py` is a simple flask app that can be patched into `Camera` instances and used for development.

Any push to the production branch will trigger a build/push action of the latest image to Docker Hub.

The new image will have to be manually pulled to the production server and the container will need to be re-launched.
