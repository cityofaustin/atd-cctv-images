# atd-cctv-images

🚧 this project is under construction 🚧

## About

Austin Transportation operates nearly 1,000 traffic cameras, which are used by the City's [Mobility Management Center](https://www.austintexas.gov/department/arterial-management) to monitor and address traffic issues in real time. The camera feeds are not recorded, and they are not used for law enforcement activites.

Although operations staff have access to live camera feeds, we have found it valuable to make intermittently-updated camera images available at a public HTTP endpoint. These images are incorporated into our own [operations dashboards](https://data.mobiltiy.austin.gov) and serve as a resource to the public, researchers, and 3rd-party services.

This module fetches thumbnail images from the traffic cameras network and uploads them to a cloud-fronted AWS S3 bucket. 

## Design and Constrainnts

The code relies on Python's [asyncio](https://docs.python.org/3/library/asyncio.html) ecosystem to achieve fast processing of hundreds of images per minute. The primary script--`process_images.py`--initaties an infite loop which fetches/uploads images from multiple cameras concurrently with the help of a pre-defined number of workers.

The image processing is designed to be resilient to various connectivty and interface issues related to external factors such as power loss, device failure, and device misconfiguration. The processing is further complicated by the fact that multiple makes and models of CCTV cameras are used on the network, each with their own API. Running asynchronous Python adds layer of complication because an uncaught worker failure can potentially hault all concurrent tasks.


## Deployment

- Docker
- network
- helper scripts
- CI
- cloudfront
- Knack dependencies

### Error handling

- `error_state` and fallback img

## Security

## Setup

## Development

- dummy api
- improvements

## TODO:

- pin package versions
- github actions

