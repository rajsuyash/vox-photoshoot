"""A runnable eval of orchestrator.py's itemized fidelity checks against real, labelled
ad output — the exact frames/clips a 2026-09-29 spike found were mis-checked (a generic
"is it the same piece" prompt passing a frame that swapped a specific necklace+earring
design for a plain chain and generic studs).

No network beyond the Anthropic API itself; skips cleanly (prints why, exits 0) if
ANTHROPIC_API_KEY is unset or a case's local file is missing — this is a report, not a
gate, so a missing fixture must never look like a failing assertion.

    .venv/bin/python fidelity_eval.py
    FIDELITY_MODEL_ADS=claude-opus-5-5 .venv/bin/python fidelity_eval.py   # A/B a model
"""

import os
import pathlib

import orchestrator

BASE = pathlib.Path('out/ads/bfd72703-059e-426b-ac32-e85a9441ded7')

# All shots in this campaign share one product: a gold filigree necklace with an ornate
# pendant and matching drop earrings (confirmed against the campaign's own
# campaign_products/pieces rows, not assumed) — out/uploads/93850216bc2f.webp.
NECKLACE_PHOTO = pathlib.Path('out/uploads/93850216bc2f.webp')
NECKLACE_DESC = 'gold filigree necklace with ornate pendant and matching drop earrings'

# The e2e ring shots' product (confirmed against ads_e2e.py's own PRODUCT_PHOTO/description).
RING_PHOTO = pathlib.Path('out/prod-ring-hero.png')
RING_DESC = 'a gold solitaire ring with a round brilliant diamond'

FRAME_CASES = [
    ('8a87bd1a (wrong jewellery)', BASE / '8a87bd1a-31b0-4ab2-ba8a-5d461d76d9ed/frame-1.png',
     [NECKLACE_PHOTO], NECKLACE_DESC, 'fail'),
    ('3e296dd5 (tray, correct)', BASE / '3e296dd5-acf6-4b58-bec6-5d8e46ebb51d/frame-1.png',
     [NECKLACE_PHOTO], NECKLACE_DESC, 'pass'),
    ('a6e0d95e (borderline, correct)', BASE / 'a6e0d95e-8397-4d4f-b446-d4b1232bcdea/frame-1.png',
     [NECKLACE_PHOTO], NECKLACE_DESC, 'pass'),
]

# (name, clip_path, approved_frame_path, product_paths, description, has_person, expected)
# has_person is read from each shot's own storyboard_shots.character_ids (DB), not guessed —
# abb7ce48, 8a87bd1a and both e2e ring shots all DO have a character_id/character_action
# ("she adjusts her hair…", "she finishes fastening the earring…", "she walks slowly…"); only
# 3e296dd5 is a genuinely product-only (no character) shot in this set.
CLIP_CASES = [
    ('a6e0d95e (earring morphs at end)',
     BASE / 'a6e0d95e-8397-4d4f-b446-d4b1232bcdea/clip-1.mp4',
     BASE / 'a6e0d95e-8397-4d4f-b446-d4b1232bcdea/frame-1.png',
     [NECKLACE_PHOTO], NECKLACE_DESC, True, 'fail'),
    ('3e296dd5 (cuts to an invented woman at 1.5s)',
     BASE / '3e296dd5-acf6-4b58-bec6-5d8e46ebb51d/clip-1.mp4',
     BASE / '3e296dd5-acf6-4b58-bec6-5d8e46ebb51d/frame-1.png',
     [NECKLACE_PHOTO], NECKLACE_DESC, False, 'fail'),
    ('8a87bd1a (wrong jewellery throughout, pose jump ~2.5s)',
     BASE / '8a87bd1a-31b0-4ab2-ba8a-5d461d76d9ed/clip-1.mp4',
     BASE / '8a87bd1a-31b0-4ab2-ba8a-5d461d76d9ed/frame-1.png',
     [NECKLACE_PHOTO], NECKLACE_DESC, True, 'fail'),
    ('abb7ce48 (window shot, no product expected)',
     BASE / 'abb7ce48-8857-4246-b4fd-4004fc4324b1/clip-1.mp4',
     BASE / 'abb7ce48-8857-4246-b4fd-4004fc4324b1/frame-1.png',
     [], '', True, 'pass'),
    ('e2e ring 1', pathlib.Path('out/ads/e2e/9be92904-272b-4646-b034-d72e2f46daa5-clip-v1.mp4'),
     pathlib.Path('out/ads/e2e/9be92904-272b-4646-b034-d72e2f46daa5-v1.png'),
     [RING_PHOTO], RING_DESC, True, 'pass'),
    ('e2e ring 2', pathlib.Path('out/ads/e2e/c9f436ca-5352-43e7-ae1a-a01f677822d1-clip-v1.mp4'),
     pathlib.Path('out/ads/e2e/c9f436ca-5352-43e7-ae1a-a01f677822d1-v1.png'),
     [RING_PHOTO], RING_DESC, True, 'pass'),
]


def _run_frame_cases():
    print(f'--- frame checks (model={orchestrator.FIDELITY_MODEL_ADS}) ---')
    correct = 0
    for name, frame_path, product_paths, description, expected in FRAME_CASES:
        if not frame_path.exists() or not all(p.exists() for p in product_paths):
            print(f'  SKIP {name}: fixture file missing')
            continue
        ok, reason, detail = orchestrator.check_frame_fidelity(
            [str(p) for p in product_paths], str(frame_path), description)
        got = 'pass' if ok else 'fail'
        hit = got == expected
        correct += hit
        mark = 'OK ' if hit else 'MISS'
        print(f'  [{mark}] {name}: expected={expected} got={got} — {reason}')
        for piece in detail.get('pieces', []):
            print(f'         piece={piece["piece"]!r} visible={piece["visible"]} '
                  f'matches={piece["matches"]} difference={piece["difference"]!r}')
    return correct, len(FRAME_CASES)


def _run_clip_cases():
    print(f'\n--- clip checks (model={orchestrator.FIDELITY_MODEL_ADS}) ---')
    correct = 0
    for name, clip_path, frame_path, product_paths, description, has_person, expected \
            in CLIP_CASES:
        if not clip_path.exists() or (frame_path and not frame_path.exists()) \
                or not all(p.exists() for p in product_paths):
            print(f'  SKIP {name}: fixture file missing')
            continue
        cuts = orchestrator.detect_cuts(clip_path)
        jumps = orchestrator.detect_pose_jump(clip_path)
        print(f'  {name}: detect_cuts={cuts} detect_pose_jump={jumps}')
        if cuts:
            got, reason = 'fail', f'cut detector: cuts to a different scene at {cuts[0]:.1f}s'
        elif jumps:
            got, reason = 'fail', f'jump detector: jumps to a different pose at {jumps[0]:.2f}s'
        else:
            ok, reason, detail = orchestrator.check_shot_clip(
                [str(p) for p in product_paths], str(frame_path) if frame_path else '',
                str(clip_path), description, has_person)
            got = 'pass' if ok else 'fail'
            for frame_result in detail.get('per_frame', []):
                print(f'         t={frame_result["t"]:.2f}s ok={frame_result["ok"]} '
                      f'reason={frame_result["reason"]!r}')
                for piece in frame_result.get('pieces', []):
                    print(f'             piece={piece["piece"]!r} visible={piece["visible"]} '
                          f'matches={piece["matches"]} difference={piece["difference"]!r}')
            if detail.get('scene'):
                print(f'         scene={detail["scene"]}')
        hit = got == expected
        correct += hit
        mark = 'OK ' if hit else 'MISS'
        print(f'  [{mark}] {name}: expected={expected} got={got} — {reason}')
    return correct, len(CLIP_CASES)


def main() -> None:
    if not os.environ.get('ANTHROPIC_API_KEY'):
        print('fidelity_eval: ANTHROPIC_API_KEY not set, skipping (nothing to grade)')
        return
    frame_correct, frame_total = _run_frame_cases()
    clip_correct, clip_total = _run_clip_cases()
    print(f'\nsummary: frames {frame_correct}/{frame_total}, clips {clip_correct}/{clip_total}')


if __name__ == '__main__':
    main()
