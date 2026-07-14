# Instructions

If you got error   , launch

```bash
rfkill list
```

If the device is soft blocked, run the following command to unblock it:

```bash
sudo rfkill unblock all

#or
sudo rfkill unblock <device_name>
```


# How to create an docker image for Raspberry Pi

From the root of the project, run the following command to build the docker image:

```bash
docker buildx create --name ixpia-builder --driver docker-container
docker buildx use ixpia-builder
docker buildx build --platform linux/arm/v7 -t cenzontle_rosora:rpi --output type=oci,dest=/home/efisio/Documents/docker_images/cenzontle_rosora.tar .
```

Copy the docker image to Raspberry Pi:

```bash
scp /home/efisio/Documents/docker_images/cenzontle_rosora.tar ixpia@ixpia.lan:/tmp/cenzontle_rosora.tar
```

On Raspberry Pi, load the docker image:

```bash
docker load -i /tmp/cenzontle_rosora.tar
```

