# twitch_vod_fetch

Script to download any time slice of a twitch.tv VoD (video-on-demand).

This is a Windows-comptatible version of Mike Kazantsev's [twitch_vod_fetch](https://github.com/mk-fg/fgtk#twitch-vod-fetch) script. This fork probably won't be actively maintained beyond my own needs.

youtube-dl - the usual tool for the job - [doesn't support neither seeking to
time nor length limits](https://github.com/rg3/youtube-dl/issues/622), but does a good job of getting a VoD m3u8 playlist
with chunks of the video (--get-url option).

Also, some chunks getting stuck here at ~10-20 KiB/s download rates, making
"sequentially download each one" approach of mpv/youtube-dl/ffmpeg/etc highly
inpractical, and there are occasional errors.

So this wrapper grabs that playlist, skips chunks according to EXTINF tags
(specifying exact time length of each) to satisfy --start-pos / --length, and
then passes all these URLs to [aria2](http://aria2.sourceforge.net/) for parallel downloading with stuff
like --max-concurrent-downloads=5, --max-connection-per-server=5,
--lowest-speed-limit=100K, etc, also scheduling retries for any failed chunks a
few times with delays.

In the end, chunks get concatenated together into one resulting mp4 file.

Process is designed to tolerate Ctrl+C and resume from any point, and allows
whatever tweaks (e.g. update url, change playlist, skip some chunks, etc), as it
keeps all the state between these in plaintext files, plus all the actual pieces.

Includes "--scatter" mode to download every-X-out-of-Y timespans instead of full
video, and has source timestamps on seeking in concatenated result (e.g. for
`-x 2:00/15:00`, minute 3 in the video will display as "16:00", making it
easier to pick timespan to download properly).

General usage examples (wrapped):
```
  > python twitch_vod_fetch.py ^
    http://www.twitch.tv/starcraft/v/15655862 sc2_wcs_ro8 ^
    http://www.twitch.tv/starcraft/v/15831152 sc2_wcs_ro4 ^
    http://www.twitch.tv/starcraft/v/15842540 sc2_wcs_finals ^
    http://www.twitch.tv/starcraft/v/15867047 sc2_wcs_lotv

  > python twitch_vod_fetch.py -x 120/15:00 ^
    http://www.twitch.tv/redbullesports/v/13263504 sc2_rb_p01_preview

  > python twitch_vod_fetch.py -s 4:22 -l 2:00 ^
    http://www.twitch.tv/redbullesports/v/13263504 sc2_rb_p01_picked_2h_chunk
```

Needs youtube-dl, requests and aria2.

A bit more info on it can be found in [this twitchtv-vods-... blog post](http://blog.fraggod.net/2015/05/19/twitchtv-vods-video-on-demand-downloading-issues-and-fixes.html).
