# Bucket root files

Static files served from the root of the public image bucket at https://cctv.austinmobility.io.

| File | Purpose |
| --- | --- |
| `index.html` | Landing page with the image URL pattern, links to the [Traffic Cameras dataset](https://data.austintexas.gov/Transportation-and-Mobility/Traffic-Cameras/b4k4-adkb/about_data), and the [terms of use](https://data.austintexas.gov/stories/s/ranj-cccq). |
| `openapi.yaml` | OpenAPI 3.0 description of the `GET /image/{camera_id}.jpg` endpoint. |

## Deploying

These files are not published by the image pipeline. Upload them manually when they change, and set the content type explicitly:

```sh
aws s3 cp index.html s3://<bucket>/index.html --content-type text/html
aws s3 cp openapi.yaml s3://<bucket>/openapi.yaml --content-type application/yaml
```

To check `openapi.yaml` before uploading, run `npx @redocly/cli lint openapi.yaml`.
