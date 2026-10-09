# TipTune

TipTune turns Chaturbate tip events into Spotify and YouTube song requests and
provides a local desktop dashboard for setup and queue control. Streamers use
the OBS integration to communicate song and request information to viewers.

The browser information display simplifies visual setup to one transparent OBS
source on the same computer. It shows the current song, upcoming queue, known
requester names, and temporary request/warning/queue notices. Existing audio
capture continues to handle sound. Older text-source setups remain available.

The approved first version uses Full and Compact presets with position, scale,
color, visibility, opacity, and motion controls; an isolated Sample/Live preview;
and automatic or manual OBS setup. It supports Spotify, YouTube, and mixed
queues. Existing settings Save behavior and unrelated work must be preserved.

Viewer information follows TipTune's queue state and recorded request
attribution. Missing attribution stays unknown. The display does not control
playback, play audio, expose raw tip messages, or infer playback progress.
