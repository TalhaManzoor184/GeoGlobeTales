# Map Reels Generator

Geography shorts (YouTube Shorts / Reels / TikTok) banane ka pipeline. Har episode ek JSON
file hai, aur script usse 1080×1920 animated map reel banati hai:

- 3D globe intro → country par zoom
- country / region highlights, glow aur pulse
- labels, markers, measurement lines, arrows aur stat badges
- Roman Urdu karaoke captions + hook title
- Urdu voiceover (edge-tts, `ur-PK-AsadNeural`), har scene voice ki length ke hisaab se stretch hota hai
- Saath mein `script.md`, `captions_roman.srt` aur `thumbnail.png` bhi banti hain

Map data [Natural Earth](https://www.naturalearthdata.com/) ka hai (public domain), aur default
mein **Pakistan point of view** (`"pov": "PAK"`) use hota hai. Is wajah se Kashmir Pakistan ke
official map ke mutabiq dikhta hai. Kisi aur market ke liye `pov` badal dein, ya hata dein
taake neutral de-facto boundaries aayen.

## Run locally

```bash
pip install -r requirements.txt          # + ffmpeg installed
python -m reels.voice  episodes/wakhan-corridor.json   # Urdu voice (internet chahiye)
python -m reels.render episodes/wakhan-corridor.json   # -> out/wakhan-corridor/wakhan-corridor.mp4
```

- Quick check frames: `python -m reels.render episodes/x.json --stills 2,10,20`
- Background music: `--music track.mp3` (12% volume par mix hota hai)
- Apni recording ya ElevenLabs voice use karni ho to `out/<slug>/voice/<scene-id>.mp3` mein
  rakhein, phir `python -m reels.voice episodes/x.json --measure-only` chalayein.

## GitHub Actions

`episodes/` mein koi JSON push karein, ya **Actions → Render reels → Run workflow** chalayein.
MP4, thumbnail, script aur SRT *reels* artifact mein mil jayenge.

## Episode format (short)

```jsonc
{
  "slug": "wakhan-corridor",
  "map": { "pov": "PAK", "center": [72, 36.5], "extent": [30, -5, 115, 70] },
  "scenes": [
    { "id": "s1", "mode": "globe", "duration": 4,
      "camera": { "from": {"lon": 5, "lat": 12, "radius": 400}, "lon": 71.5, "lat": 36, "radius": 1900 },
      "title": "Afghanistan ki ye *dum* kyun hai?",       // *word* = yellow
      "vo_ur": "…Urdu script for TTS…", "vo_roman": "…Roman Urdu captions…" },
    { "id": "s2", "duration": 6, "camera": { "lon": 68.5, "lat": 35.6, "width_km": 1900 },
      "hide": ["pin"],
      "show": [
        { "id": "afg", "type": "country", "iso": "AFG", "fill": "orange", "alpha": 0.55 },
        { "id": "wakhan", "type": "region", "of": "AFG", "clip": [71.55, 36.4, 75.2, 37.75],
          "fill": "yellow", "glow": true, "pulse": true, "at": 2.3 }
      ] }
  ]
}
```

Layer types: `country` (iso: code ya list), `region` (country ka clipped hissa), `label`,
`marker`, `measure` (do mulkon ke beech sab se kam faasla, data se nikalta hai), `arrow`, `badge`.
Har layer `at` second par fade in hoti hai aur `hide` hone tak rehti hai. Colours: `yellow green
blue red orange purple teal white grey` ya koi bhi hex. ISO codes Natural Earth ke `ADM0_A3` hain.
