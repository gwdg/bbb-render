#!/usr/bin/python3

import logging
logging.basicConfig(level=logging.DEBUG)

from decimal import Decimal
import argparse
import os
import sys
import cairosvg
from intervaltree import Interval, IntervalTree
from collections import namedtuple

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
gi.require_version('GES', '1.0')
from gi.repository import GLib, GObject, Gst, GstPbutils, GES

import xml.etree.ElementTree as ET
ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


def file_to_uri(path):
    path = os.path.realpath(path)
    return 'file://' + path


def minmax(low, val, high):
    return max(low, min(val, high))


def to_ns(val, digits=3):
    """ Converts seconds (int, float, str) to Gstreamer native timestamps
        (nanoseconds) while avoiding most floating-point rounding errors.

        Input values are natively rounded to 3 decimal places (milliseconds) by
        default.
    """
    return int(round(Decimal(val), digits) * Gst.SECOND)


class Presentation:
    """ Presentation to xges project renderer.

        Notes: All public APIs accept timestamps and durations as seconds. All
        internal APIs use Gst native resolution (nanoseconds).

    """

    def __init__(self, source, size):
        self._asset_cache = {}
        self._layer_cache = {}

        self.asset_path = os.path.abspath(source)
        self.xml_meta = ET.parse(self._asset_path('metadata.xml'))

        self.name = self.xml_meta.find("./meta/meetingName").text.strip()
        self.presentation_length = float(
            self.xml_meta.find('./playback/duration').text.strip()
        ) / 1000

        self.width, self.height = size
        self._opening_credits = []
        self._closing_credits = []
        self._cut = 0, to_ns(self.presentation_length)

        # Setup timeline, project, and audio/video tracks
        self.timeline = GES.Timeline.new_audio_video()
        self.video_track, self.audio_track = self.timeline.get_tracks()
        if self.video_track.type == GES.TrackType.AUDIO:
            self.video_track, self.audio_track = self.audio_track, self.video_track
        self.project = self.timeline.get_asset()

        self.set_track_caps(fps=24, hz=48000)
        self.set_project_metadata("name", self.name)
        self.init_layers("Credits", "Camera", "Slides", "Deskshare", "Backdrop")

    def cut(self, start, end=0):
        """ Define which part of the presentation should be rendered.

            Both start and end time are in seconds from the beginning.
            If end 0 or negative, it is counted from the end of the presentation."""
        if end <= 0:
            end += to_ns(self.presentation_length)
        self._cut = to_ns(start), to_ns(end)

    def set_project_metadata(self, name, value):
        self.project.register_meta_string(
            GES.MetaFlag.READWRITE, name, value)

    def set_track_caps(self, fps=24, hz=48000):
        """ Set frame rate and audio sampling rate """
        self.video_track.props.restriction_caps = Gst.Caps.from_string(
            f'video/x-raw(ANY), width=(int){self.width}, height=(int){self.height}, '
            f'framerate=(fraction){fps}/1')
        self.audio_track.props.restriction_caps = Gst.Caps.from_string(
            f'audio/x-raw(ANY), rate=(int){hz}, channels=(int)2')

        vp8_preset = Gst.ElementFactory.make('vp8enc', 'vp8_preset')
        vp8_preset.set_property('threads', 8)
        vp8_preset.set_property('token-partitions', 2)
        vp8_preset.set_property('target-bitrate', 2500000)
        vp8_preset.set_property('deadline', 0)  # best
        vp8_preset.set_property('end-usage', 2)  # Constant Quality Mode
        vp8_preset.set_property('cq-level', 10)
        Gst.Preset.save_preset(vp8_preset, 'vp8_preset')

        profile = GstPbutils.EncodingContainerProfile.new(
            'default', 'bbb-render encoding profile',
            Gst.Caps.from_string('video/webm'))
        profile.add_profile(GstPbutils.EncodingVideoProfile.new(
            Gst.Caps.from_string('video/x-vp8'), 'vp8_preset',
            self.video_track.props.restriction_caps, 0))
        profile.add_profile(GstPbutils.EncodingAudioProfile.new(
            Gst.Caps.from_string('audio/x-opus'), None,
            self.audio_track.props.restriction_caps, 0))
        self.project.add_encoding_profile(profile)

    def _add_clip(self, layer, asset, *, ts, dt, pos, size, skip=0):
        """ Displays and asset on a specific layer at timestamp `ts` for `dt`
             seconds, skipping the first `skip` seconds. The asset is shown at
             position `pos` stretched to `size`. """
        logging.info("Clip %s %r ts=%d dt=%d skip=%d pos=%r size=%r",
            layer, asset, ts, dt, skip, pos, size)

        layer = self._get_layer(layer)
        asset = self._get_asset(asset)
        asset_duration = self._get_duration(asset)
        #dt = minmax(0, dt, asset_duration - skip)

        clip = layer.add_asset(asset, ts, skip, dt, GES.TrackType.UNKNOWN)
        for element in clip.find_track_elements(self.video_track, GES.TrackType.VIDEO, GObject.TYPE_NONE):
            element.set_child_property("posx", pos[0])
            element.set_child_property("posy", pos[1])
            element.set_child_property("width", size[0])
            element.set_child_property("height", size[1])

    def _get_layer(self, name):
        return self._layer_cache[name]

    def _asset_path(self, name):
        if not os.path.isabs(name):
            name = os.path.join(self.asset_path, name)
        return os.path.realpath(name)

    def _get_asset(self, name):
        path = self._asset_path(name)
        uri = file_to_uri(path)
        asset = self._asset_cache.get(uri)
        if asset is None:
            asset = GES.UriClipAsset.request_sync(uri)
            self.project.add_asset(asset)
            self._asset_cache[uri] = asset
        return asset

    def _get_size(self, asset):
        if isinstance(asset, str):
            asset = self._get_asset(asset)
        info = asset.get_info()
        video_info = info.get_video_streams()[0]
        return (video_info.get_width(), video_info.get_height())

    def _get_duration(self, asset):
        """ Return asset play duration """
        if isinstance(asset, str):
            asset = self._get_asset(asset)
        return asset.props.duration

    def fit(self, asset, box, align="cc", shrink_only=False):
        """ Fit and align an asset in a bounding box.

            The asset can be a file path, a GES.Asset, or a (w,h) tuple.
            The box can have two elements (w,h) or four (x,y,w,h).
            Alignment is a two-character string (l|c|r + t|c|b).

            Returns (x,y,w,h)
        """

        rect = asset
        if isinstance(rect, (str, GES.Asset)):
            rect = self._get_size(rect)
        aw, ah = rect

        if len(box) == 2:
            box = [0,0] + list(box)
        bx, by, bw, bh = box

        x,y,w,h = bx, by, aw, ah

        if not (shrink_only and aw <= bw and ah <= bh):
            # Asset needs to be resized to fit box
            scale = (aw / ah) / (bw / bh)
            if scale > 1: # Asset wider than box
                w, h = bw, round(bh / scale)
            else:         # Asset taller than box
                w, h = round(bw*scale), bh

        if align[0] == "c":
            x += (bw-w)//2
        elif align[0] == "r":
            x += (bw-w)
        if align[1] == "c":
            y += (bh-h)//2
        elif align[1] == "b":
            y += (bh-h)

        return x, y, w, h

    def init_layers(self, *layers):
        for name in layers:
            layer = self.timeline.append_layer()
            layer.register_meta_string(GES.MetaFlag.READWRITE, 'video::name', name)
            self._layer_cache[name] = layer

    @property
    def _opening_credits_length(self):
        return sum(duration for (skip, duration, asset) in self._opening_credits)

    @property
    def _closing_credits_length(self):
        return sum(duration for (skip, duration, asset) in self._closing_credits)

    @property
    def _total_length(self):
        return self._opening_credits_length + self._cut[1] - self._cut[0] + self._closing_credits_length

    def add_webcams(self, fit, align):
        video = 'video/webcams.webm'
        box = self.fit(video, fit, align)

        ts = self._opening_credits_length
        skip = self._cut[0]
        dt = self._cut[1] - skip

        self._add_clip("Camera", video,
            ts=ts, dt=dt, skip=skip,
            pos=box[:2], size=box[2:])

    def add_slides(self, fit, align):
        maxsize = fit[2:]
        skip = self._cut[0]
        maxdt = self._cut[1] - skip

        for png, start, duration in self._generate_slides(maxsize):
            # start and duration are already cut to the desired presentation time frame
            size = self._get_size(png)
            box = self.fit(size, fit, align)
            start += self._opening_credits_length

            self._add_clip("Slides", png,
              ts=start, dt=duration,
              pos=box[:2], size=box[2:])

    def _generate_slides(self, maxsize):
        """ Yield (png_path, start_time, duration) for each version of each
          slide, in order. Both start and duration are in seconds.

          This honors cut(start, end) and only returns slides and timings that
          fit into the configured timeframe. """

        start_ts, end_ts = self._cut

        doc = ET.parse(self._asset_path('shapes.svg'))
        for img in doc.iterfind('./{http://www.w3.org/2000/svg}image'):
            logging.debug("Found slide: %s", img.get("id"))

            path = img.get('{http://www.w3.org/1999/xlink}href')
            img_start = to_ns(img.get('in'))
            img_end = to_ns(img.get('out'))
            img_width = int(img.get('width'))
            img_height = int(img.get('height'))
            size = self.fit((img_width, img_height), (0, 0, maxsize[0], maxsize[1]))[2:]

            if path.endswith('/deskshare.png'):
                logging.info("Skipping: Slides invisible during deskshare")
                continue

            if img_start >= end_ts or img_end <= start_ts:
                logging.info("Skipping: Slide not in presentation time frame")
                continue

            # Cut slide duration to presentation time frame
            img_start = max(img_start, start_ts)
            img_end =  min(img_end, end_ts)

            # Fix backgfound image path
            img.set('{http://www.w3.org/1999/xlink}href', self._asset_path(path))

            # Find an SVG group with shapes belonging to this slide.
            canvas = doc.find('./{{http://www.w3.org/2000/svg}}g[@class="canvas"][@image="{}"]'.format(img.get('id')))

            if canvas is None:
                # No annotations, just a slide.
                png = self._render_slide([img], size, f'{img.get("id")}-0.png')
                yield png, img_start, img_end-img_start
                continue

            # Collect shapes. Each shape can have multiple draw-steps with the same
            # `shape` id and only the most recent version is visible.
            shapes = {} # id -> [(start, undo, shape), ...]
            for shape in canvas.iterfind('./{http://www.w3.org/2000/svg}g[@class="shape"]'):
                shape_id = shape.get('shape')
                shape_style = shape.get('style')
                shape.set('style', shape_style.replace('visibility:hidden;', ''))

                # Poll results are embedded as images. Make the href absolute.
                for shape_img in shape.iterfind('./{http://www.w3.org/2000/svg}image'):
                    shape_img.set('{http://www.w3.org/1999/xlink}href',
                        self._asset_path(shape_img.get('{http://www.w3.org/1999/xlink}href')))

                start = to_ns(shape.get('timestamp'))
                undo = to_ns(shape.get('undo'))
                shapes.setdefault(shape_id, []).append((start, undo, shape))

            # Build timeline of shapes and draw-steps during this slide
            timeline = IntervalTree()
            timeline.add(Interval(begin=img_start, end=img_end, data=[]))

            # For each shape-id, order draw-steps by start-time and calculate end-time.
            for shape_id, shapes in shapes.items():
                shapes = sorted(shapes) # sort by start time
                zindex = shapes[0][0] # Use start time for z-layer ordering (new on top)

                for i, (start, undo, shape) in enumerate(shapes):
                    # When switching back to an old slides, shape start-time is way too small
                    start = max(img_start, start)
                    end = img_end

                    if i+1 < len(shapes):
                        # Hide non-final draw-steps when replaced by the next draw-step.
                        end = shapes[i+1][0]
                    elif undo > 0:
                        # Shape was erased, so hide it earlier
                        end = undo

                    if end <= start:
                        continue # May happen if self._cut removed parts of a slide livetime
                    if start >= img_end:
                        loging.warning("Shape timing is off: start=%d end=%s", start/Gst.SECOND, end/Gst.SECOND)
                        continue # Should not happen, but who knows

                    timeline.add(Interval(begin=start, end=end, data=[(zindex, shape)]))

            # In multiuser-canvas mode, shape drawing may overlap in time. This
            # split+merge step ensure that we have non-overlapping time slices, each
            # containing all shapes that are visible in that slice.
            timeline.split_overlaps()
            timeline.merge_overlaps(data_reducer=lambda a,b: a+b)

            # Render one PNG per time slice
            for i, interval in enumerate(sorted(timeline)):
                shapes = [shape for zindex, shape in sorted(interval.data)]
                png = self._render_slide([img] + shapes, size, f'{img.get("id")}-{i}.png')
                yield png, interval.begin, interval.end-interval.begin

    def _render_slide(self, layers, size, name):
        path = self._asset_path(name)
        if not os.path.exists(path):
            svg = ET.XML(f'<svg version="1.1" xmlns="http://www.w3.org/2000/svg"></svg>')

            # Scale to desired size but keep coordinates
            bg = layers[0] # Use first (bottom) layer as reference frame
            bgw, bgh = int(bg.get("width")), int(bg.get("height"))
            svg.set('viewBox', f'0 0 {bgw} {bgh}')
            svg.set("width", str(size[0]))
            svg.set("height", str(size[1]))

            for layer in layers:
                svg.append(layer)

            cairosvg.svg2png(bytestring=ET.tostring(svg), write_to=path)

        return path

    def add_deskshare(self, fit, align):
        video = self._asset_path('deskshare/deskshare.webm')
        if not os.path.exists(video):
            return

        doc = ET.parse(self._asset_path('deskshare.xml'))
        events = doc.findall('./event')

        duration = self._get_duration(video)
        tsoffset = self._opening_credits_length
        cut_start, cut_end = self._cut
        box = self.fit(video, fit, align)

        for event in events:
            share_start = to_ns(event.get('start_timestamp'))
            share_end = to_ns(event.get('stop_timestamp'))
            # These are useless? The actual video is bigger, and runtime size
            #   changes are not reflected in the xml :/
            # video_width = int(event.get('video_width'))
            # video_height = int(event.get('video_height'))
            if share_start >= share_end or share_end > duration:
                continue # Bad data?
            if share_end <= cut_start or share_start >= cut_end:
                continue # Not within time frame

            ts = tsoffset + share_start - cut_start
            skip = max(cut_start, share_start)
            dt = min(cut_end, share_end) - skip

            self._add_clip('Deskshare', video,
                ts=ts, skip=skip, dt=dt,
                pos=box[:2], size=box[2:])

    def add_backdrop(self, image):
        self._add_clip("Backdrop", image,
            ts=0, dt=self._total_length,
            pos=(0, 0), size=(self.width, self.height))

    def add_opening_credits(self, fname, skip=0, duration=0):
        skip = to_ns(skip)
        duration = to_ns(duration)
        tsoffset = self._opening_credits_length
        maxdt = self._get_duration(fname) - skip
        duration = min(duration or maxdt, maxdt)

        self._add_clip('Credits', fname,
            ts=tsoffset, dt=duration,
            pos=(0,0), size=(self.width, self.height))

        self._opening_credits.append((skip, duration, fname))

    def add_closing_credits(self, fname, skip=0, duration=0):
        skip = to_ns(skip)
        duration = to_ns(duration)
        tsoffset = self._total_length
        maxdt = self._get_duration(fname) - skip
        duration = min(duration or maxdt, maxdt)

        self._add_clip('Credits', fname,
            ts=tsoffset, dt=duration,
            pos=(0,0), size=(self.width, self.height))

        self._closing_credits.append((skip, duration, fname))

    def save(self, target):
        self.timeline.commit_sync()
        self.timeline.save_to_uri(file_to_uri(target), None, True)


parser = argparse.ArgumentParser(description='convert a BigBlueButton presentation into a GES project')
parser.add_argument('--size', metavar='WIDTHxHEIGHT', type=str, default="1920x1080",
                    help='Video width and height')
parser.add_argument('--margin', metavar='WIDTH', type=int, default=10,
                    help='Space between and around webcam and presentation areas.')

parser.add_argument('--start', metavar='SECONDS', type=float, default=0,
                    help='Seconds to skip from the start of the recording')
parser.add_argument('--end', metavar='SECONDS', type=float, default=0,
                    help='End point in the recording')
parser.add_argument('--webcam-width', metavar='WIDTH', type=float, default=0.2,
                    help='Width of the webcam area in pixel, or as a fraction.')

parser.add_argument('--backdrop', metavar='FILE', type=str, default=None,
                    help='Backdrop image for the project')

parser.add_argument('--opening-credits', metavar='FILE',
                    type=str, action='append', default=[],
                    help='File to use as opening credits (may be repeated)')
parser.add_argument('--closing-credits', metavar='FILE',
                    type=str, action='append', default=[],
                    help='File to use as closing credits (may be repeated)')

parser.add_argument('basedir', metavar='PATH', type=str,
                    help='Directory containing BBB presentation assets')
parser.add_argument('target', metavar='FILE', type=str,
                    help='Output filename for GES project')

def main(argv):
    Gst.init(None)
    GES.init()

    opts = parser.parse_args(argv[1:])
    source = opts.basedir
    width, height = tuple(map(int, opts.size.split("x", 2)))

    p = Presentation(source=source, size=(width, height))

    if opts.start or opts.end:
        p.cut(opts.start, opts.end)

    for fname in opts.opening_credits or []:
        p.add_opening_credits(fname)

    if opts.backdrop:
        p.add_backdrop(opts.backdrop)

    for fname in opts.closing_credits or []:
        p.add_closing_credits(fname)

    margin = opts.margin
    cam_width = opts.webcam_width
    if 0 < cam_width < 1:
        cam_width = int(cam_width * width)

    max_height = int(height - 2 * margin)
    slides_width = int(width - 2 * margin)

    if cam_width > 0:
        slides_width -= int(cam_width + margin)
        p.add_webcams(fit=(slides_width + 2 * margin, margin, cam_width, max_height), align="lt")

    p.add_slides(fit=(margin, margin, slides_width, max_height), align="ct")
    p.add_deskshare(fit=(margin, margin, slides_width, max_height), align="ct")

    p.save(opts.target)

if __name__ == '__main__':
    sys.exit(main(sys.argv))
