# Say "Engels" and "Yudame" right; presentations vertical by default; redo the site tour

Tom, valor team chat, 2026-10-22 09:29 UTC (voice note on the valorengels.com
tour, https://cap.yuda.me/s/f11k06jdw1v65q0): both names are said wrong, and
presentations should be vertical by default instead of landscape. This
replaces his 2026-10-17 preference for landscape decks.

Done when Tom watches the new tour and hears both names said right.

## What gets built

**1. Pronunciation, in yudame/skills `present/tts.py` (PR from valorengels).**
Every voice Valor makes goes through this file's `Kokoro.wav`: narrated videos,
and voice-note replies (`tools/voice.py`, the kernel's `VALOR_TTS_VOICE`).
kokoro-onnx turns text into phonemes with espeak and also takes
`create(..., is_phonemes=True)`. Add a small `LEXICON` near `DEFAULT_KOKORO_VOICE`:

- `Engels`: Friedrich Engels' name, hard g, `ˈɛŋɡəlz` (ENG-gulz).
- `Yudame`: Spanish *ayúdame* without the a, `juˈdɑme` (yoo-DAH-meh).

`Kokoro.wav` splits the text on those words, matched case-insensitively,
including inside a longer token (`valorengels.com`), and also matches the
domain spelling `yuda.me` (said as Yudame). Each non-name piece goes through
the engine's own tokenizer (`self.k.tokenizer.phonemize`), and each name is
replaced with its lexicon phonemes. The result goes to `create` with
`is_phonemes=True`. With no match, the text goes to `create` unchanged, as
it does today.

`phonemize` strips each piece (`normalize_text` and a final `.strip()`), so
the splice adds the gaps back: a piece that ended in whitespace in the text
gets one space after its phonemes, and one that started with whitespace
gets one before. "I'm Valor Engels" becomes `aɪm vˈælɚ ˈɛŋɡəlz`, not
`…vˈælɚˈɛŋɡəlz`. The fragments around a match are handled too:
- a possessive `'s` / `s` straight after a name adds `z` to the name's
  phonemes rather than being phonemized alone;
- punctuation alone in a piece (`.`, `,`, `?`, `!`, `:`, `;`) is copied
  through as itself, which is how Kokoro's vocabulary marks pauses, rather
  than phonemized, since espeak may return nothing for it;
- in a domain, `.com` and `.me` are said "dot com" / "dot me".

The split is a pure function, `splice(text, phonemize)`, at module level, so
it can be tested without kokoro_onnx.

Text, captions and the manifest keep the real spellings. The IPA is tuned by
ear: render each name alone, in a sentence, as a possessive, and in
`valorengels.com`, then transcribe each clip back with the local Parakeet
(`tools/voice.py`). The real espeak phonemes for each fragment case above are
printed and checked too, not only the test stub's. The delivery reports what
Parakeet heard. `say` stays as is: it runs only when the Kokoro model files
are missing.

The yudame/skills work is done in a fresh clone under the task workspace,
not in `~/.claude/plugins/marketplaces/yudame`, which live turns load from.

**2. Vertical by default.**
- yudame/skills `present/SKILL.md`: a deck is portrait, one slide per page, on
  the same 108×192 mm page as documents, with type sized for that page.
  Landscape (192×108) only when someone asks for it. Update the `pdfinfo` line
  to match.
- `present/video/src/Root.tsx`: 1080×1920. Update `Explainer.tsx` layout
  (maxWidth, type sizes, padding) so a title and points fit a phone-shaped
  frame. `VIDEO.md` says the template is vertical; for landscape, swap width
  and height and re-check the frames.
- house-style states no deck shape (checked), so it is unchanged.
- This repo: `persona/delivery.md` and `docs/persona.md` "What Valor makes for
  people" gain one clause saying decks and narrated videos are vertical unless
  landscape is asked for, with Tom's 2026-10-22 words. `converse.md` names no
  shape, so it is unchanged.

**3. The tour, redone.** Its source is not on this Mac: not in the site task's
workspace, not in `~/Desktop/Valor`, and the other Macs can't be reached from
here. The Cap page serves the original MP4 (`v.cap.so/.../result.mp4`), so:
1. Download it and ffprobe it.
2. Transcribe the narration with Parakeet to get the script.
3. Pull a frame from each scene to see its visuals.
4. Fix anything now wrong against the live site. If the narration says
   `yuda.me` or `cap.yuda.me`, either the lexicon covers it (above) or the
   line is rewritten to say the company name.
5. Rebuild it in the present template, vertical, with the fixed `tts.py`: same
   content and scenes, and phone-width screenshots of valorengels.com where
   the original showed the site.
6. Run VIDEO.md's checks, plus a Parakeet pass over every scene that says
   either name.
7. Upload to Cap from Valor's logged-in Chrome (BYOB) at cap.yuda.me. Report
   the new share link, the MP4 path under `deliverables/`, its ffprobe
   duration, and its sha256.

yudame/skills has no CI. Its checks are run by hand, as its README says:
`python3 tests/check_skills.py` and `sh tests/check_install.sh`, plus the
new `python3 tests/check_tts.py`. The PR lists all three results, and Valor
merges it once they pass (as with #1 to #3). Voice replies only get the fix
once this Mac's marketplace checkout updates (`claude plugin marketplace
update yudame`). The delivery says when that happened, and checks it by
rendering a voice reply from the live path, not by assuming it.

## Out of scope

- Pronunciation for the `say` fallback.
- Other names (Counsell, client names) until one is heard wrong.
- Re-rendering earlier decks or videos.

## Tests

- yudame/skills: `check_skills.py` and `check_install.sh` pass. A new
  `tests/check_tts.py` is plain Python like check_skills.py: no pytest, no
  kokoro_onnx import. It loads `splice` from `present/tts.py` (whose
  kokoro import is already late) and runs it with a stub phonemizer that
  strips its input, as the real one does. It asserts:
  - the boundary spaces: "I'm Valor Engels" has a space before the name's
    phonemes;
  - each name in title case, lower case and all caps;
  - `valorengels.com`, `yudame.com` and `yuda.me` inside a domain;
  - possessives `Yudame's` / `Engels's`, which append `z`;
  - a name followed by `.` or `,`, which keeps the punctuation;
  - a sentence with neither name, which comes back unchanged.
  README gets the command beside the other two.
- Ear check: run Parakeet on a clip saying "I'm Valor Engels, at Yudame". The
  transcript should come back close to "Engels" / "Yudame" and not "Angles" /
  "Udemy". Report what it heard, word for word.
- Video: 1080×1920, duration equals the sum of the scenes, audio is not
  silent, and one frame per scene has been looked at.
- This repo: the full suite passes (persona rendering tests included).

Stakes: a shared skill's voice and default shape, plus a public video. Both
can be undone, and a mistake is audible right away.
