# atd-cctv-images

## About

Austin Transportation operates nearly 1,000 traffic cameras, which are used by the City's [Mobility Management Center](https://www.austintexas.gov/department/arterial-management) to monitor and address traffic issues in real time. The camera feeds are not recorded, and they are not used for law enforcement activites.

Although operations staff have access to live camera feeds, we have found it valuable to make intermittently-updated camera images available at a public HTTP endpoint. These images are incorporated into our own [operations dashboards](https://data.mobiltiy.austin.gov) and serve as a resource to the public, researchers, and 3rd-party services.

This module fetches thumbnail images from the traffic cameras network and uploads them to a cloud-fronted AWS S3 bucket. 

## Resources

- Images are available at `https://cctv.austinmobility.io/image/< camera-id >.jpg`

- The [public traffic camera dataset](https://data.austintexas.gov/Transportation-and-Mobility/Traffic-Cameras/b4k4-adkb) provides a listing of all available cameras

- The [traffic cameras dashboards(https://data.mobiltiy.austin.gov) is a map for searching/viewing cameras

## Design and Constraints

The image processing is designed to be resilient to various connectivty and interface issues related to external factors such as power loss, device failure, and device misconfiguration. The processing is further complicated by the fact that multiple makes and models of CCTV cameras are used on the network, each with their own API. Running asynchronous Python adds layer of complication because an uncaught worker failure can potentially hault all concurrent tasks.

The code relies on Python's [asyncio](https://docs.python.org/3/library/asyncio.html) ecosystem to achieve fast processing of hundreds of images per minute. The primary script--`process_images.py`--initaties concurrent, looping [`tasks`](https://docs.python.org/3/library/asyncio-task.html#task-object) which fetch and upload images from cameras.

Each camera-task loops infinitely, sleeping for 5 minutes between each loop. When a new image is uploaded, it's [`Expires`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Expires) header is set to five minutes into the future, which allows AWS Cloudfront to appropriately cache an image until a new image should be available. If a camera repeatedly fails to fetch/upload a new image, the camera's infinite loop is terminated, and no further processing will occur for that camera until the script is restarted.


## Deployment

- scripts
- CI

```
tail -f /var/log/cctv-images/cctv.log 
```

### Error handling

- `error_state` and fallback img

## Development

- dummy api
- improvements

## TODO:

- pin package versions
