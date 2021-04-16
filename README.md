# BigBlueButton Presentation Renderer

The BigBlueButton web conferencing system provides the ability to
record meetings. Rather than producing a single video file though, it
produces multiple assets (webcam footage, screenshare footage, slides,
scribbles, chat, etc) and relies on a web player to assemble them.

This project provides some scripts to download the assets for a
recorded presentation, and assemble them into a single video suitable
for archive or upload to other video hosting sites.

## Prerequisites

The scripts are written in Python and rely on the GStreamer Editing
Services libraries (GES). On an Ubuntu 20.04 system, you will need to
install at least the following:

```
sudo apt install python3-gi gir1.2-ges-1.0 ges1.0-tools libgirepository1.0-dev
```

## Downloading a presentation

The first script will download the presentation assets locally:

```
./download.py presentation_url outdir
```

The `presentation_url` should be a full URL containing the string
`/playback/presentation/2.0/playback.html?meetingId=`.  This will
download the presentation metadata, video footage and slides.


## Create a GES project

The second script combines the downloaded assets into a GStreamer
Editing Services project.

```
$ ./make-xges.py asset_path presentation.ges
```

You can control the resulting video size, starta nd end timestamps, margins, background image, opening and closing credits and more via command line parameters. See `./make-xges.py -h` for details.

Currently the project includes the following aspects of the BBB
recording:

* [x] Webcam audio and video
* [x] Screensharing
* [x] Slides
* [x] Whiteboard scribbles (excluding text)

Currently not supported (but planned):

* [ ] Whiteboard text-boxes
* [ ] Mouse cursor position
* [ ] Text chat

The project can be previewed using the `ges-launch-1.0` command line tool:

```
ges-launch-1.0 --load presentation.xges
```

or rendered into a video with:

```
ges-launch-1.0 --load presentation.xges -o videofile.webm
```

# Licence

This work is based on https://github.com/plugorgau/bbb-render.
The original Licence still applies (see `LICENCE` file).

