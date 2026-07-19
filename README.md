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

# How to create a docker image for Raspberry Pi

From the root of the project, run the following the script to copy the necessary files

```bash
./tools/prepare_image.sh root@<raspberry_pi_ip>
```

Then, run the following command to build the docker image on your Raspberry Pi:

```bash
docker build -t rosora:rpi .
```

Once the image is built, you can run it with the docker-compose file provided

```bash
docker-compose up
```


