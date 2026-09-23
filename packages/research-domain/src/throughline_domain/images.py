"""
Image ↔ image — figure similarity (taxonomy pair 5).

**The governing rule: never assert misconduct.** Every positive outcome routes
to *needs review* and is phrased as similarity. The vocabulary is *visually
similar*, *shares a region with*, *warrants human inspection* — never
*manipulated*, *fabricated*, or *duplicated fraudulently*.

That is not squeamishness. The exposure from a false accusation is asymmetric
and severe: a wrong "unrelated" costs a reviewer nothing, a wrong "fabricated"
can end a career, and no confidence level makes the second one a safe word for a
machine. A researcher can look at two panels and decide. This system's job is to
put the pair in front of them.

Everything here is deterministic — perceptual hashing, no vision model. That is
a real limit, stated rather than hidden: this can tell you two images are
pixel-related. It cannot tell you whether two microscopy panels show the same
specimen, and it says so rather than guessing.
"""

from __future__ import annotations

from typing import Any

from throughline_schemas.words import counted
from .image_safety import UnsafeImage, checked_dimensions
from .verdicts import RunState, Verdict

#: Hamming distance thresholds on a 64-bit perceptual hash.
#:
#: Tuned to be *specific* rather than sensitive. A false "these are duplicates"
#: is a serious accusation to put in front of a researcher about a colleague's
#: figure, so the bar to say anything at all is deliberately high.
IDENTICAL = 0
NEAR_DUPLICATE = 6
SIMILAR = 12

#: Below this, a perceptual hash is comparing compression artifacts rather than
#: content, and any verdict would be noise dressed as analysis.
MIN_DIMENSION = 64


class ImageError(RuntimeError):
    """An image could not be compared."""


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _grey(path: str, size: int):
    from PIL import Image

    checked_dimensions(path)
    with Image.open(path) as image:
        image = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
        import numpy as np

        return np.asarray(image, dtype=float)


def difference_hash(path: str, size: int = 8) -> int:
    """
    A dHash: each bit is "is this pixel brighter than the one to its right".

    Gradient-based rather than absolute, so it survives the exposure and
    contrast changes that a figure picks up passing through a journal's
    production pipeline — differences that are not evidence of anything.
    """
    pixels = _grey(path, size + 1)[:, :]
    bits = pixels[:, 1:] > pixels[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def average_hash(path: str, size: int = 8) -> int:
    """A second, independent hash. Two agreeing is worth more than one."""
    pixels = _grey(path, size)
    mean = pixels.mean()
    value = 0
    for bit in (pixels > mean).flatten():
        value = (value << 1) | int(bit)
    return value


def hamming(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def dimensions(path: str) -> tuple[int, int]:
    return checked_dimensions(path)


def _transform_hashes(path: str) -> dict[str, int]:
    """
    Hashes of the eight rigid transforms of an image.

    A rotated or flipped panel has a completely different hash from the
    original, so without this the most obvious kind of reuse is invisible —
    which is exactly the case I3 exists for.
    """
    from PIL import Image

    import numpy as np

    checked_dimensions(path)
    with Image.open(path) as image:
        base = image.convert("L").resize((9, 9), Image.Resampling.LANCZOS)
        variants = {
            "as is": base,
            "rotated 90°": base.transpose(Image.Transpose.ROTATE_90),
            "rotated 180°": base.transpose(Image.Transpose.ROTATE_180),
            "rotated 270°": base.transpose(Image.Transpose.ROTATE_270),
            "flipped horizontally": base.transpose(
                Image.Transpose.FLIP_LEFT_RIGHT),
            "flipped vertically": base.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
        }

    hashes = {}
    for name, variant in variants.items():
        pixels = np.asarray(variant, dtype=float)
        bits = pixels[:, 1:] > pixels[:, :-1]
        value = 0
        for bit in bits.flatten():
            value = (value << 1) | int(bit)
        hashes[name] = value
    return hashes


# ---------------------------------------------------------------------------
# I4 — partial overlap, via keypoint matching
# ---------------------------------------------------------------------------

#: Matched keypoints needed before a shared region is worth mentioning.
#:
#: High on purpose. Two micrographs of the same tissue type share a great deal
#: of local texture without sharing any pixels, and a low bar here would flag
#: every pair in a paper. The cost of a false positive is a researcher being
#: asked to defend a figure that is fine.
MIN_MATCHES = 22

#: Proportion of matches that must agree on one geometric transform. A genuine
#: shared region produces matches that move together; coincidental texture
#: matches scatter.
MIN_INLIER_RATIO = 0.55


def shared_region(left_path: str, right_path: str) -> dict[str, Any] | None:
    """
    Do these two images contain a common region?

    ORB keypoints plus a RANSAC homography. This is the case a whole-image hash
    cannot see by construction: a panel spliced into the corner of a larger
    figure changes every bit of a global hash while sharing a real region.

    Returns evidence or None. Never a conclusion — the caller phrases it as
    similarity, and a person decides what it means.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        # Absent is a capability, not an error: hashing still works and the
        # interface says which checks did not run.
        return None

    checked_dimensions(left_path)
    checked_dimensions(right_path)
    left = cv2.imread(left_path, cv2.IMREAD_GRAYSCALE)
    right = cv2.imread(right_path, cv2.IMREAD_GRAYSCALE)
    if left is None or right is None:
        return None

    orb = cv2.ORB_create(nfeatures=1500)
    left_kp, left_desc = orb.detectAndCompute(left, None)
    right_kp, right_desc = orb.detectAndCompute(right, None)
    if left_desc is None or right_desc is None:
        return None
    if len(left_kp) < MIN_MATCHES or len(right_kp) < MIN_MATCHES:
        return None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(left_desc, right_desc, k=2)

    # Lowe's ratio test: a keypoint whose best match is barely better than its
    # second best has matched texture, not a location.
    good = [m for m, n in (p for p in pairs if len(p) == 2)
            if m.distance < 0.72 * n.distance]
    if len(good) < MIN_MATCHES:
        return None

    src = np.float32([left_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([right_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if mask is None:
        return None

    inliers = int(mask.sum())
    ratio = inliers / len(good)
    if inliers < MIN_MATCHES or ratio < MIN_INLIER_RATIO:
        return None

    return {"matches": len(good), "inliers": inliers, "ratio": round(ratio, 2)}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """
    Compare two figures.

    `left` and `right` each need `path`, `title` and optionally `content_hash`.
    Returns a typed verdict whose family is `needs_review` for every positive
    outcome — the researcher decides, this only puts the pair in front of them.
    """
    for side in (left, right):
        if not side.get("path"):
            raise ImageError("Both images need a readable file.")

    try:
        left_size = dimensions(left["path"])
        right_size = dimensions(right["path"])
    except UnsafeImage as exc:
        raise ImageError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — an unreadable image is an answer
        raise ImageError(f"That file could not be read as an image ({exc}).") from exc

    titles = {"left_location": left.get("title", "the first image"),
              "right_location": right.get("title", "the second image")}

    # I9 — below a certain size a hash compares compression artifacts.
    if min(*left_size, *right_size) < MIN_DIMENSION:
        return _verdict(
            "I9", "below_assessable_resolution", 0.9, titles,
            caveats=[f"One image is {min(*left_size, *right_size)}px on its "
                     f"shortest side. Below {MIN_DIMENSION}px a perceptual hash "
                     "compares compression artifacts rather than content."],
            remedies=["Compare the original files rather than thumbnails."])

    # I1 — the same bytes. Often entirely legitimate reuse.
    if (left.get("content_hash") and right.get("content_hash")
            and left["content_hash"] == right["content_hash"]):
        return _verdict("I1", "identical_bytes", 0.99, titles,
                        remedies=["Confirm whether the reuse is intended and "
                                  "attributed."])

    left_d, right_d = difference_hash(left["path"]), difference_hash(right["path"])
    left_a, right_a = average_hash(left["path"]), average_hash(right["path"])
    distance = hamming(left_d, right_d)
    second = hamming(left_a, right_a)

    # I2 — the same picture, different file.
    if distance <= IDENTICAL and second <= 2:
        return _verdict("I2", "perceptually_identical", 0.9, titles,
                        caveats=["The files differ but the images do not."],
                        remedies=["Confirm whether the same panel appearing "
                                  "twice is intended."])

    # I3 — the same picture under a rigid transform.
    transforms = _transform_hashes(right["path"])
    for name, value in transforms.items():
        if name == "as is":
            continue
        if hamming(left_d, value) <= NEAR_DUPLICATE:
            return _verdict(
                "I3", f"near_duplicate_{name.replace(' ', '_')}", 0.75,
                {**titles, "transform": name},
                caveats=["Detected by comparing perceptual hashes of rigid "
                         "transforms. It states a property of the pixels and "
                         "nothing about how they came to be that way."],
                remedies=["Look at both panels side by side and decide."])

    if distance <= NEAR_DUPLICATE:
        return _verdict(
            "I3", "near_duplicate", 0.7, {**titles, "transform": "a close crop "
                                          "or rescale"},
            caveats=[f"Perceptual distance {distance} of 64."],
            remedies=["Look at both panels side by side and decide."])

    # I4 — a shared region. Checked after the whole-image cases and before
    # "similar", because a partial overlap is a stronger statement than a
    # resemblance and a weaker one than a duplicate.
    overlap = shared_region(left["path"], right["path"])
    if overlap:
        return _verdict(
            "I4", "shared_region", 0.65, titles,
            caveats=[f"{overlap['inliers']} keypoints in these two images agree "
                     f"on a single geometric transform ({overlap['ratio']:.0%} "
                     "of the candidate matches). That is the pattern a common "
                     "region produces.",
                     "It states a property of the pixels. What it means about "
                     "the figures is for you to judge."],
            remedies=["Open both panels and look at the region they share."])

    # I7 — related but not the same.
    if distance <= SIMILAR:
        return _verdict(
            "I7", "similar_but_distinct", 0.6, titles,
            caveats=[f"Perceptual distance {distance} of 64 — close enough to "
                     "share structure, far enough to be different data. Two "
                     "panels from one experiment usually look like this."])

    # I8 — nothing detected. A real and useful answer.
    return _verdict(
        "I8", "no_relationship_detected", 0.8, titles,
        caveats=[f"Perceptual distance {distance} of 64.",
                 "This compares pixels. It cannot tell whether two panels show "
                 "the same specimen, and does not claim to."])


def _verdict(code: str, reason: str, confidence: float,
             facts: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    body = Verdict(outcome_code=code, reason_code=reason, confidence=confidence,
                   facts=facts, method="deterministic", **kwargs)
    payload = body.to_dict()
    # Stated on every result, not only the positive ones, because the vocabulary
    # limit is a property of the whole feature.
    payload["language_note"] = (
        "Phrased as similarity. This system never asserts that an image was "
        "manipulated or fabricated: it can tell you two pictures are related in "
        "their pixels, and only a person can say what that means.")
    return payload


# ---------------------------------------------------------------------------
# Several images at once
# ---------------------------------------------------------------------------

MAX_IMAGES = 20


def compare_many(images: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Every pair among a set of figures.

    The pairwise count is reported for the same reason it is in the paper
    matrix: 20 images is 190 comparisons, and at that many, one flagged pair is
    what chance produces. Without the denominator a single hit reads as a
    finding.
    """
    if len(images) < 2:
        raise ImageError("Comparing needs at least two images.")
    if len(images) > MAX_IMAGES:
        raise ImageError(
            f"{len(images)} images would mean "
            f"{len(images) * (len(images) - 1) // 2} comparisons. Compare at "
            f"most {MAX_IMAGES} at a time.")

    import itertools

    pairs = []
    failed = []
    for left, right in itertools.combinations(images, 2):
        try:
            verdict = compare(left, right)
        except ImageError as exc:
            failed.append({"left": left.get("title"), "right": right.get("title"),
                           "reason": str(exc)})
            continue
        pairs.append({
            "left": left.get("title"), "right": right.get("title"),
            "left_id": left.get("id"), "right_id": right.get("id"),
            "verdict": verdict,
        })

    flagged = [p for p in pairs
               if p["verdict"]["outcome"] in ("I1", "I2", "I3", "I4", "I5")]

    return {
        "flagged": flagged,
        "pairs": pairs,
        "failed": failed,
        "multiplicity": {
            "images": len(images),
            "comparisons": len(pairs),
            "flagged": len(flagged),
            "note": (f"{len(images)} images means {len(pairs)} comparisons. "
                     f"{counted(len(flagged), 'pair')} warrant a look. At this many "
                     "comparisons a single hit is not remarkable on its own — "
                     "it is a prompt to open both panels."),
        },
        "method": "deterministic",
        "language_note": (
            "Everything here is phrased as similarity. This system never "
            "asserts that an image was manipulated or fabricated. It compares "
            "pixels; a person decides what that means."),
        "limits": (
            "Perceptual hashing detects reuse, rescaling, rotation and "
            "flipping; keypoint matching detects a region two figures share. "
            "Neither can tell whether two different photographs show the same "
            "specimen, and neither reads what a figure means — those need a "
            "human eye or a vision model, and this reports neither rather than "
            "guessing."),
        "checks_run": _checks_run(),
    }


def _checks_run() -> dict[str, Any]:
    """Which comparisons were available, so absence is never silent (§123)."""
    try:
        import cv2  # noqa: F401
        keypoints = True
    except ImportError:
        keypoints = False
    return {
        "perceptual_hash": True,
        "rigid_transforms": True,
        "shared_region": keypoints,
        "note": None if keypoints else (
            "OpenCV is not installed, so shared-region detection did not run. "
            "A panel spliced into part of another figure would not be found."),
    }


__all__ = ["IDENTICAL", "MIN_INLIER_RATIO", "MIN_MATCHES", "shared_region", "MAX_IMAGES", "MIN_DIMENSION", "NEAR_DUPLICATE",
           "SIMILAR", "ImageError", "average_hash", "compare", "compare_many",
           "difference_hash", "dimensions", "hamming"]
