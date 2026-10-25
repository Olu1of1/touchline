"""Verifier: nothing reaches the listener unless every number and name in it is backed by the facts.

A blind listener cannot glance at a scoreboard to catch a mistake, so the
co-commentator's lines are checked before they are spoken:

  * every number (digits, number words, ordinals) must match a value in the fact pack
    (exact for counts and scores, within 1 for percentages, within 0.05 for decimals);
  * every player or club named must appear in the fact pack (no invented people);
  * lines must be short enough to fit a stoppage.

A rejected line is replaced by a template line built directly from the facts, or dropped.
"""

import re

NUM_WORDS = {"zero": 0, "nil": 0, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
             "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
ORD_WORDS = {"third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
             "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
             "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20}
MAX_WORDS = 45


def _flatten(obj, out):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten(v, out)
    elif isinstance(obj, str):
        for m in re.findall(r"\d+(?:\.\d+)?", obj):
            out.append(float(m))


def _names(obj, out):
    """Every word of every string value in the facts counts as a name the speaker may use."""
    if isinstance(obj, dict):
        for v in obj.values():
            _names(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _names(v, out)
    elif isinstance(obj, str):
        out.update(w.strip(".,;:'’()") for w in obj.split())


def _rows(obj, out):
    """Table rows in the facts: lists of plain values holding at least one name and one number."""
    if isinstance(obj, dict):
        for v in obj.values():
            _rows(v, out)
    elif isinstance(obj, (list, tuple)):
        scalars = all(isinstance(v, (str, int, float)) or v is None for v in obj)
        if scalars and any(isinstance(v, str) for v in obj) and any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in obj):
            words = set()
            nums = []
            for v in obj:
                if isinstance(v, str):
                    words.update(w.strip(".,;:'’()") for w in v.split())
                elif isinstance(v, (int, float)):
                    nums.append(abs(float(v)))
            out.append((words, nums))
        else:
            for v in obj:
                _rows(v, out)


CLAUSE_SPLIT = re.compile(r"[;.]\s|, (?=[A-Z])|\band\b|\bbut\b|\bwhile\b|\bwhereas\b")


def _row_binding(text, facts, league_names):
    """A number said next to a name must come from that name's row, when the facts hold a table with that name."""
    rows = []
    _rows(facts, rows)
    if not rows:
        return []
    problems = []
    for clause in CLAUSE_SPLIT.split(text):
        names = {t.strip("'’") for t in re.findall(r"[A-ZÀ-Ý][\wÀ-ÿ'’-]+", clause)} & league_names
        if not names:
            continue
        mine = [nums for words, nums in rows if words & names]
        if not mine:
            continue
        for m in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)", clause):
            if re.search(r"\bper\s*$", clause[:m.start()]):          # "xG per 90" names a stat, it isn't a claim
                continue
            v = float(m.group(1))
            tol = 0.05 if "." in m.group(1) else 1e-9
            if not any(abs(n - v) <= tol for nums in mine for n in nums):
                problems.append(f"{m.group(1)} is not in the row for {', '.join(sorted(names))}")
    return problems


def verify(text, facts, league_names):
    """Return (ok, problems). `league_names` is every player/club name token in the league."""
    problems = []
    allowed = []
    _flatten(facts, allowed)
    words = text.split()
    if len(words) > MAX_WORDS:
        problems.append(f"too long ({len(words)} words)")

    def ok_num(v, is_pct=False, is_dec=False):
        for a in allowed + [abs(x) for x in allowed if x < 0]:     # "21.2 points short" states the size of -21.2
            if is_dec and abs(a - v) <= 0.05:
                return True
            if is_pct and abs(a - v) <= 1.0:
                return True
            if abs(a - v) < 1e-9:
                return True
        return False

    for m in re.finditer(r"(\d+(?:\.\d+)?)(\s*(?:%|percent|per cent))?", text):
        v = float(m.group(1))
        if not ok_num(v, is_pct=bool(m.group(2)), is_dec="." in m.group(1)):
            problems.append(f"unsupported number {m.group(0).strip()}")
    for m in re.finditer(r"\b(\d+)(st|nd|rd|th)\b", text):
        if not ok_num(float(m.group(1))):
            problems.append(f"unsupported ordinal {m.group(0)}")
    low = re.findall(r"[a-z]+", text.lower())
    for w in low:
        if w in NUM_WORDS and not ok_num(NUM_WORDS[w]):
            problems.append(f"unsupported number word '{w}'")
        if w in ORD_WORDS and not ok_num(ORD_WORDS[w]):
            problems.append(f"unsupported ordinal word '{w}'")

    named = set()
    _names(facts, named)
    for tok in re.findall(r"[A-ZÀ-Ý][\wÀ-ÿ'’-]+", text):
        tok = tok.strip("'’")
        if tok in league_names and tok not in named:
            problems.append(f"unsupported name {tok}")
    problems += _row_binding(text, facts, set(league_names))
    # de-duplicate, keep order
    seen, uniq = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return (not uniq), uniq
